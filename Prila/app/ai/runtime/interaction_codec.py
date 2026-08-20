from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.schemas.conversation import Interaction


AnswerKind = Literal[
    "free_text",
    "confirm",
    "single_select",
    "multi_select",
    "form",
]


class InteractionAnswer(BaseModel):
    """
    Нормализованный ответ пользователя на конкретный Interaction.

    Transport/API должен передавать interaction_id из последнего публичного
    Interaction. Благодаря этому старый или повторно отправленный UI-event
    не сможет случайно продолжить другой workflow.
    """

    model_config = ConfigDict(extra="forbid")

    interaction_id: str = Field(min_length=1)
    kind: AnswerKind

    text: str | None = None
    confirmed: bool | None = None
    selected_values: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self) -> "InteractionAnswer":
        if self.kind == "free_text":
            if not self.text or not self.text.strip():
                raise ValueError(
                    "free_text answer requires non-empty text"
                )

            if (
                self.confirmed is not None
                or self.selected_values
                or self.fields
            ):
                raise ValueError(
                    "free_text answer may contain only text"
                )

        elif self.kind == "confirm":
            if self.confirmed is None:
                raise ValueError(
                    "confirm answer requires confirmed boolean"
                )

            if (
                self.text is not None
                or self.selected_values
                or self.fields
            ):
                raise ValueError(
                    "confirm answer may contain only confirmed"
                )

        elif self.kind == "single_select":
            if len(self.selected_values) != 1:
                raise ValueError(
                    "single_select answer requires exactly one value"
                )

            if (
                self.text is not None
                or self.confirmed is not None
                or self.fields
            ):
                raise ValueError(
                    "single_select answer may contain only "
                    "selected_values"
                )

        elif self.kind == "multi_select":
            if not self.selected_values:
                raise ValueError(
                    "multi_select answer requires at least one value"
                )

            if (
                self.text is not None
                or self.confirmed is not None
                or self.fields
            ):
                raise ValueError(
                    "multi_select answer may contain only "
                    "selected_values"
                )

        elif self.kind == "form":
            if not self.fields:
                raise ValueError(
                    "form answer requires fields"
                )

            if (
                self.text is not None
                or self.confirmed is not None
                or self.selected_values
            ):
                raise ValueError(
                    "form answer may contain only fields"
                )

        return self


class DecodedInteractionAnswer(BaseModel):
    """
    Валидированный ответ с удобным domain-представлением для workflow.

    value содержит только допустимую полезную нагрузку:
    - str для free_text;
    - bool для confirm;
    - str для single_select;
    - list[str] для multi_select;
    - dict[str, Any] для form.
    """

    model_config = ConfigDict(extra="forbid")

    interaction_id: str
    kind: AnswerKind
    value: str | bool | list[str] | dict[str, Any]


class InteractionAnswerError(ValueError):
    """
    Ошибка пользовательского input.

    API преобразует её в понятный 4xx или повтор того же Interaction.
    Это не node/workflow failure.
    """


def decode_interaction_answer(
    *,
    interaction: Interaction,
    answer: InteractionAnswer,
) -> DecodedInteractionAnswer:
    """
    Проверяет:
    1. ответ относится к текущему Interaction;
    2. совпадает тип input;
    3. выбранные значения существуют в опубликованных options;
    4. заполнены обязательные поля формы.

    Проверка нужна даже при UI с кнопками: API может получить устаревший,
    подменённый либо вручную сформированный запрос.
    """
    if answer.interaction_id != interaction.interaction_id:
        raise InteractionAnswerError(
            "Ответ относится к устаревшему или другому вопросу."
        )

    if answer.kind != interaction.kind:
        raise InteractionAnswerError(
            "Тип ответа не соответствует ожидаемому вопросу."
        )

    if interaction.kind == "free_text":
        return DecodedInteractionAnswer(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            value=(answer.text or "").strip(),
        )

    if interaction.kind == "confirm":
        return DecodedInteractionAnswer(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            value=bool(answer.confirmed),
        )

    if interaction.kind == "single_select":
        selected = answer.selected_values[0]
        allowed_values = {
            option.value
            for option in interaction.options
        }

        if selected not in allowed_values:
            raise InteractionAnswerError(
                "Выбранный вариант отсутствует в списке."
            )

        return DecodedInteractionAnswer(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            value=selected,
        )

    if interaction.kind == "multi_select":
        allowed_values = {
            option.value
            for option in interaction.options
        }

        selected_values = list(
            dict.fromkeys(answer.selected_values)
        )

        unknown_values = [
            value
            for value in selected_values
            if value not in allowed_values
        ]

        if unknown_values:
            raise InteractionAnswerError(
                "Среди выбранных вариантов есть недоступные."
            )

        return DecodedInteractionAnswer(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            value=selected_values,
        )

    if interaction.kind == "form":
        required_names = {
            field.name
            for field in interaction.fields
            if field.required
        }

        submitted_names = set(answer.fields)
        missing_names = required_names - submitted_names

        if missing_names:
            missing_labels = [
                field.label
                for field in interaction.fields
                if field.name in missing_names
            ]

            raise InteractionAnswerError(
                "Не заполнены обязательные поля: "
                + ", ".join(missing_labels)
            )

        allowed_names = {
            field.name
            for field in interaction.fields
        }

        unknown_names = submitted_names - allowed_names

        if unknown_names:
            raise InteractionAnswerError(
                "Форма содержит неизвестные поля."
            )

        normalized_fields = {
            name: value.strip()
            if isinstance(value, str)
            else value
            for name, value in answer.fields.items()
        }

        return DecodedInteractionAnswer(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            value=normalized_fields,
        )

    raise InteractionAnswerError(
        f"Неподдерживаемый тип interaction: {interaction.kind!r}"
    )