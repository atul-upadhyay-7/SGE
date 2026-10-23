"""Committed-row metrics, bounded rolling rate and failure behaviour."""
from unittest.mock import patch

from app.core.config import Settings
from app.schemas.telemetry import RejectionRecord
from app.services.device_cache import DeviceCache
from app.services.ingestion_service import IngestionService


def test_rate_uses_recent_committed_samples_and_expires():
    with patch('app.services.ingestion_service.monotonic', return_value=100):
        service = IngestionService(None, DeviceCache(), Settings())
    service._recent_stores.extend([(101, 10), (150, 20)])
    service.stored = 30
    with patch('app.services.ingestion_service.monotonic', return_value=160):
        stats = service.stats()
        assert stats['stored_per_second'] == .5
        assert stats['counter_scope'] == 'current_process'
    with patch('app.services.ingestion_service.monotonic', return_value=211):
        assert service.stats()['stored_per_second'] == 0
        assert service.stats()['stored'] == 30


async def test_failed_commit_does_not_increment_persisted_counters():
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, *args, **kwargs): pass
        async def commit(self): raise RuntimeError('simulated commit failure')
        async def rollback(self): pass
    from unittest.mock import AsyncMock
    service = IngestionService(Session, DeviceCache(), Settings())
    with patch('app.services.ingestion_service.TelemetryRepository') as repo:
        repo.return_value.insert_rejections = AsyncMock()
        await service._flush([RejectionRecord(reason='invalid_payload', source='http')])
    assert service.stats()['rejected'] == 0
    assert service.stats()['write_errors'] == 1
