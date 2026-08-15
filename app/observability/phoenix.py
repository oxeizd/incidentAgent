import logging
import os
import sys
import warnings
from contextlib import redirect_stdout
from pathlib import Path
import phoenix as px
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
import asyncio

logger = logging.getLogger(__name__)

# Подавление SAWarning от SQLAlchemy (Phoenix internal)
warnings.filterwarnings("ignore", message="Skipped unsupported reflection")

TRACES_BACKUP_DIR = Path("data/runtime/phoenix_traces/")

def start_phoenix(load_existing: bool = True):
    """Launch Phoenix, optionally loading saved traces. Minimal logging."""
    trace_dataset = None
    if load_existing and TRACES_BACKUP_DIR.exists():
        try:
            trace_dataset = px.TraceDataset.load(directory=str(TRACES_BACKUP_DIR))
            logger.info(f"Загружены сохранённые трассы из {TRACES_BACKUP_DIR}")
        except Exception as e:
            logger.warning(f"Не удалось загрузить сохранённые трассы: {e}")

    # Запуск Phoenix — подавляем прямой print() библиотеки
    with redirect_stdout(open(os.devnull, "w")):
        if trace_dataset:
            px.launch_app(trace=trace_dataset)
        else:
            px.launch_app()

    logger.info("Phoenix запущен на http://localhost:6006")

    # Настройка экспорта трасс в Phoenix через OTLP
    tracer_provider = TracerProvider()
    otlp_exporter = OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces")
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    otel_trace.set_tracer_provider(tracer_provider)

    # Инструментируем LangChain (без логов)
    LangChainInstrumentor().instrument()

async def stop_phoenix_and_save_traces():
    """Save current traces to disk and stop Phoenix."""
    await asyncio.sleep(1)

    try:
        client = px.Client()
        trace_dataset = client.get_trace_dataset()
        if trace_dataset:
            TRACES_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            trace_dataset.save(directory=str(TRACES_BACKUP_DIR))
            logger.info(f"Трассы сохранены в {TRACES_BACKUP_DIR}")
        else:
            logger.info("Нет трасс для сохранения")
    except Exception as e:
        logger.warning(f"Не удалось сохранить трассы: {e}")
    finally:
        px.close_app()
        logger.info("Phoenix остановлен")