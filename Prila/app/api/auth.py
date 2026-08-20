from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Literal

from cryptography import x509
from cryptography.x509.oid import NameOID
from fastapi import HTTPException, Request, status

from app.config import settings


logger = logging.getLogger(__name__)


AuthMode = Literal[
    "local",
    "certificate",
]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """
    Trusted request identity.

    user_id is the ownership key for threads, messages, presentation drafts,
    search-result snapshots and LangGraph conversation checkpoints.
    """

    user_id: str
    source: AuthMode
    common_name: str | None = None


class UserResolver:
    """
    Resolves application identity from a trusted source.

    local mode uses LOCAL_USER_ID and ignores request headers.
    certificate mode uses the CN in X-Forwarded-Client-Cert. The upstream
    TLS proxy must verify the certificate and overwrite this header.
    """

    def __init__(
        self,
        *,
        mode: AuthMode,
        local_user_id: str | None,
        cache_ttl_seconds: int,
        max_header_length: int,
    ) -> None:
        if mode not in {"local", "certificate"}:
            raise ValueError(
                f"Unsupported authentication mode: {mode!r}"
            )

        if cache_ttl_seconds < 1:
            raise ValueError(
                "cache_ttl_seconds must be at least 1"
            )

        if max_header_length < 1:
            raise ValueError(
                "max_header_length must be at least 1"
            )

        normalized_local_user_id = (
            local_user_id.strip()
            if isinstance(local_user_id, str)
            else ""
        )

        if mode == "local" and not normalized_local_user_id:
            raise ValueError(
                "LOCAL_USER_ID is required when AUTH_MODE=local"
            )

        self._mode = mode
        self._local_user_id = normalized_local_user_id
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_header_length = max_header_length

        self._cache: dict[str, tuple[float, CurrentUser]] = {}
        self._lock = threading.Lock()

    def resolve(
        self,
        request: Request,
    ) -> CurrentUser:
        if self._mode == "local":
            return CurrentUser(
                user_id=self._local_user_id,
                source="local",
            )

        certificate_header = request.headers.get(
            "X-Forwarded-Client-Cert",
        )

        user = self._resolve_certificate(certificate_header)
        forwarded_user_id = request.headers.get(
            "X-User-ID",
            "",
        ).strip()

        if forwarded_user_id and forwarded_user_id != user.user_id:
            logger.warning(
                "Identity mismatch: certificate_cn=%s x_user_id=%s "
                "method=%s path=%s",
                user.user_id,
                forwarded_user_id,
                request.method,
                request.url.path,
            )

        return user

    def _resolve_certificate(
        self,
        certificate_header: str | None,
    ) -> CurrentUser:
        if not certificate_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Клиентский сертификат не передан. "
                    "Использование приложения без сертификата невозможно."
                ),
            )

        normalized_header = certificate_header.strip()

        if not normalized_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Клиентский сертификат не передан. "
                    "Использование приложения без сертификата невозможно."
                ),
            )

        if len(normalized_header) > self._max_header_length:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Некорректный размер сертификата клиента.",
            )

        cached = self._get_cached(normalized_header)

        if cached is not None:
            return cached

        try:
            certificate_pem = _extract_pem(normalized_header)
            certificate = x509.load_pem_x509_certificate(
                certificate_pem.encode("utf-8")
            )
            attributes = certificate.subject.get_attributes_for_oid(
                NameOID.COMMON_NAME
            )

            if not attributes:
                raise ValueError(
                    "Certificate COMMON_NAME is missing"
                )

            common_name = str(attributes[0].value).strip()

            if not common_name:
                raise ValueError(
                    "Certificate COMMON_NAME is empty"
                )

            if len(common_name) > 200:
                raise ValueError(
                    "Certificate COMMON_NAME is too long"
                )
        except Exception:
            logger.warning(
                "Could not parse forwarded client certificate.",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Не удалось определить пользователя "
                    "из клиентского сертификата."
                ),
            ) from None

        user = CurrentUser(
            user_id=common_name,
            source="certificate",
            common_name=common_name,
        )

        self._put_cached(normalized_header, user)
        return user

    def _get_cached(
        self,
        certificate_header: str,
    ) -> CurrentUser | None:
        now = time.monotonic()

        with self._lock:
            item = self._cache.get(certificate_header)

            if item is None:
                return None

            expires_at, user = item

            if expires_at > now:
                return user

            self._cache.pop(certificate_header, None)
            return None

    def _put_cached(
        self,
        certificate_header: str,
        user: CurrentUser,
    ) -> None:
        expires_at = time.monotonic() + self._cache_ttl_seconds

        with self._lock:
            self._cache[certificate_header] = (
                expires_at,
                user,
            )

            if len(self._cache) > 2_000:
                self._remove_expired_locked()

    def _remove_expired_locked(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, (expires_at, _) in self._cache.items()
            if expires_at <= now
        ]

        for key in stale:
            self._cache.pop(key, None)


def create_user_resolver() -> UserResolver:
    return UserResolver(
        mode=settings.AUTH_MODE,
        local_user_id=settings.LOCAL_USER_ID,
        cache_ttl_seconds=(
            settings.CERT_IDENTITY_CACHE_TTL_SECONDS
        ),
        max_header_length=(
            settings.CERT_IDENTITY_MAX_HEADER_LENGTH
        ),
    )


def _extract_pem(
    certificate_header: str,
) -> str:
    """
    Supports raw PEM and XFCC format:

    Hash=...;Cert="-----BEGIN%20CERTIFICATE-----%0A..."
    """
    if "-----BEGIN CERTIFICATE-----" in certificate_header:
        return certificate_header.strip().strip('"')

    parsed = urllib.parse.parse_qs(
        certificate_header.replace(";", "&"),
        keep_blank_values=False,
        strict_parsing=False,
    )

    values = parsed.get("Cert") or parsed.get("cert") or []

    if not values or not values[0]:
        raise ValueError(
            "Cert parameter is absent in XFCC header"
        )

    certificate_pem = values[0].strip().strip('"')

    if "-----BEGIN CERTIFICATE-----" not in certificate_pem:
        raise ValueError(
            "XFCC Cert parameter does not contain PEM"
        )

    return certificate_pem
