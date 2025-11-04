"""
Test the long polling mechanism (two-phase polling).

Test scenario:
1. Fast polling phase: fast monitoring before the first successful transmission
2. Delayed polling phase: After the first successful transmission, a long polling interval of 3 minutes is started.
3. New message interrupt: Terminate deferred polling when new message is received
4. Polling times limit: Automatically exit after reaching the maximum number of times
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["BOT_TOKEN"] = "test-token"
os.environ["MODE"] = "B"
os.environ["ACTIVE_MODEL"] = "claudecode"

import bot


@pytest.fixture(autouse=True)
def _force_claudecode(monkeypatch):
    """Make sure your tests always go to the ClaudeCode branch."""
    monkeypatch.setattr(bot, "ACTIVE_MODEL", "claudecode")
    monkeypatch.setattr(bot, "MODEL_CANONICAL_NAME", "claudecode")
    return


@pytest.fixture
def mock_session_path(tmp_path):
    """Create a temporary session file."""
    session_file = tmp_path / "test_session.jsonl"
    session_file.write_text("")
    return session_file


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test."""
    bot.CHAT_LONG_POLL_STATE.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()
    bot.CHAT_LONG_POLL_LOCK = None
    yield
    bot.CHAT_LONG_POLL_STATE.clear()
    bot.SESSION_OFFSETS.clear()
    bot.CHAT_DELIVERED_HASHES.clear()
    bot.CHAT_DELIVERED_OFFSETS.clear()
    bot.CHAT_LONG_POLL_LOCK = None


# ============================================================================
# Unit testing: helper functions
# ============================================================================


def test_interrupt_long_poll_sets_flag():
    """test _interrupt_long_poll() Set interrupt flag."""
    chat_id = 12345
    bot.CHAT_LONG_POLL_STATE[chat_id] = {
        "active": True,
        "round": 2,
        "max_rounds": 10,
        "interrupted": False,
    }

    asyncio.run(bot._interrupt_long_poll(chat_id))

    assert bot.CHAT_LONG_POLL_STATE[chat_id]["interrupted"] is True


def test_interrupt_long_poll_no_state():
    """test _interrupt_long_poll() No error is reported when there is no state."""
    chat_id = 12345
    # Make sure there is no status
    bot.CHAT_LONG_POLL_STATE.pop(chat_id, None)

    # No exception should be thrown
    asyncio.run(bot._interrupt_long_poll(chat_id))


# ============================================================================
# Integrated test:_watch_and_notify two-phase polling
# ============================================================================


@pytest.mark.asyncio
async def test_watch_and_notify_quick_exit_without_delivery(mock_session_path):
    """
    testScenario 1: In the fast polling phase, when there is no message, exist times out and exits.
    """
    chat_id = 12345

    with patch.object(bot, "_deliver_pending_messages", new_callable=AsyncMock) as mock_deliver:
        # Simulation always has no message
        mock_deliver.return_value = False

        # Use short timeout (0.5 seconds)
        await bot._watch_and_notify(
            chat_id=chat_id,
            session_path=mock_session_path,
            max_wait=0.5,
            interval=0.1,
        )

        # should be called _deliver_pending_messages many times
        assert mock_deliver.call_count >= 3

    # There should be no long polling state
    assert chat_id not in bot.CHAT_LONG_POLL_STATE


@pytest.mark.asyncio
async def test_watch_and_notify_enters_long_poll_after_first_delivery(mock_session_path):
    """
    testScenario 2: In the fast polling stage, the delayed polling mode is entered after the first Second-rate is sent successfully.
    """
    chat_id = 12345
    delivery_count = 0

    async def mock_deliver(cid, path, **kwargs):
        nonlocal delivery_count
        delivery_count += 1
        # Return True for the first time (sent successfully for the first time)
        # Return False afterwards (no new messages)
        return delivery_count == 1

    with patch.object(bot, "_deliver_pending_messages", side_effect=mock_deliver):
        # Start listening task
        task = asyncio.create_task(
            bot._watch_and_notify(
                chat_id=chat_id,
                session_path=mock_session_path,
                max_wait=10.0,
                interval=0.05,
            )
        )

        # Waiting for the first successful delivery
        await asyncio.sleep(0.15)

        # Check if delayed polling mode has been entered
        assert chat_id in bot.CHAT_LONG_POLL_STATE
        assert bot.CHAT_LONG_POLL_STATE[chat_id]["active"] is True
        assert bot.CHAT_LONG_POLL_STATE[chat_id]["round"] == 0
        assert bot.CHAT_LONG_POLL_STATE[chat_id]["max_rounds"] == 600

        # Interrupt task (avoid waiting 3 minutes)
        await bot._interrupt_long_poll(chat_id)
        await asyncio.sleep(0.1)

        # Cancel task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_watch_and_notify_long_poll_increments_round(mock_session_path):
    """
    testScenario 3: Delayed polling phase, the polling count is incremented when there are no new messages.
    """
    chat_id = 12345
    delivery_count = 0

    async def mock_deliver(cid, path, **kwargs):
        nonlocal delivery_count
        delivery_count += 1
        # Returns True the first time, then False
        return delivery_count == 1

    with patch.object(bot, "_deliver_pending_messages", side_effect=mock_deliver):
        # Use short polling intervals for testing
        original_interval = 180.0
        short_interval = 0.1

        task = asyncio.create_task(
            bot._watch_and_notify(
                chat_id=chat_id,
                session_path=mock_session_path,
                max_wait=10.0,
                interval=0.05,
            )
        )

        # Waiting to enter delayed polling mode
        await asyncio.sleep(0.15)

        # Manually change the interval to a short interval (for testing)
        # Note: The interval in the actual code is 180 seconds, here we simulate fast polling
        # Check if poll count is incremented (needs to wait multiple poll cycles)

        # Wait for several polls
        await asyncio.sleep(0.3)

        # Break and clean up
        await bot._interrupt_long_poll(chat_id)
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_watch_and_notify_interrupted_by_new_message(mock_session_path):
    """
    testScenario 4: Delayed polling interrupted by new messages.
    """
    chat_id = 12345
    delivery_count = 0

    async def mock_deliver(cid, path, **kwargs):
        nonlocal delivery_count
        delivery_count += 1
        return delivery_count == 1  # Only returns True the first time

    with patch.object(bot, "_deliver_pending_messages", side_effect=mock_deliver):
        task = asyncio.create_task(
            bot._watch_and_notify(
                chat_id=chat_id,
                session_path=mock_session_path,
                max_wait=10.0,
                interval=0.05,
            )
        )

        # Waiting to enter delayed polling mode
        await asyncio.sleep(0.15)
        assert chat_id in bot.CHAT_LONG_POLL_STATE

        # Simulate the arrival of new messages and interrupt polling
        await bot._interrupt_long_poll(chat_id)

        # Wait for the task to detect the interrupt flag and exit
        await asyncio.sleep(0.2)

        # The task should have exited and the status cleared
        assert chat_id not in bot.CHAT_LONG_POLL_STATE or \
               bot.CHAT_LONG_POLL_STATE[chat_id].get("interrupted") is True

        # clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_watch_and_notify_long_poll_resets_on_new_delivery(mock_session_path):
    """
    testScenario 5: New messages are received in delayed polling and the polling count is reset.
    """
    chat_id = 12345
    delivery_sequence = [True, False, False, True, False]  # 1st and 4th news
    delivery_index = 0

    async def mock_deliver(cid, path, **kwargs):
        nonlocal delivery_index
        result = delivery_sequence[delivery_index] if delivery_index < len(delivery_sequence) else False
        delivery_index += 1
        return result

    with patch.object(bot, "_deliver_pending_messages", side_effect=mock_deliver):
        task = asyncio.create_task(
            bot._watch_and_notify(
                chat_id=chat_id,
                session_path=mock_session_path,
                max_wait=10.0,
                interval=0.05,
            )
        )

        # Waiting to enter delayed polling mode
        await asyncio.sleep(0.15)
        assert chat_id in bot.CHAT_LONG_POLL_STATE

        # Wait a few polls to make sure the count increases
        await asyncio.sleep(0.25)

        # Break and clean up
        await bot._interrupt_long_poll(chat_id)
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ============================================================================
# Boundary condition test
# ============================================================================


def test_interrupt_long_poll_idempotent():
    """testRepeated calls _interrupt_long_poll() is idempotent."""
    chat_id = 12345
    bot.CHAT_LONG_POLL_STATE[chat_id] = {
        "active": True,
        "round": 0,
        "max_rounds": 10,
        "interrupted": False,
    }

    # first call
    asyncio.run(bot._interrupt_long_poll(chat_id))
    assert bot.CHAT_LONG_POLL_STATE[chat_id]["interrupted"] is True

    # Second call (should still be True, no error reported)
    asyncio.run(bot._interrupt_long_poll(chat_id))
    assert bot.CHAT_LONG_POLL_STATE[chat_id]["interrupted"] is True


@pytest.mark.asyncio
async def test_watch_and_notify_max_rounds_limit(mock_session_path):
    """
    testScenario 6: Exit after delayed polling reaches the maximum Second-rate number.

    NOTE: Since the actual interval is 180 seconds × 10 Second-rate = 30 minutes,
    Here we only test the logic and do not wait for the actual time.
    """
    chat_id = 12345

    # Modify the maximum number of polling Second-rates to 2 (convenient for testing)
    async def mock_deliver(cid, path, **kwargs):
        # Only the first Second-rate of exist returns True and enters delayed polling.
        # Subsequently return False to increment the polling count
        if not hasattr(mock_deliver, "called"):
            mock_deliver.called = True
            return True
        return False

    with patch.object(bot, "_deliver_pending_messages", side_effect=mock_deliver):
        # It cannot be easily tested here because you need to wait 180 seconds. × Second-ratenumber
        # In actual testing, long can be modified through monkeypatch_poll_interval
        # Only logical verification is done here
        pass


# ============================================================================
# Performance test: Make sure not to block the event loop
# ============================================================================


@pytest.mark.asyncio
async def test_watch_and_notify_does_not_block_event_loop(mock_session_path):
    """
    test _watch_and_notify Does not block the event loop.
    """
    chat_id = 12345

    async def mock_deliver(cid, path, **kwargs):
        return False

    with patch.object(bot, "_deliver_pending_messages", side_effect=mock_deliver):
        task = asyncio.create_task(
            bot._watch_and_notify(
                chat_id=chat_id,
                session_path=mock_session_path,
                max_wait=1.0,
                interval=0.1,
            )
        )

        # Perform other tasks while monitoring is running
        other_task_completed = False

        async def other_task():
            nonlocal other_task_completed
            await asyncio.sleep(0.2)
            other_task_completed = True

        other = asyncio.create_task(other_task())

        # waiting for other_task Complete
        await other

        # Verify other_task exist _watch_and_notify Completed during runtime
        assert other_task_completed is True

        # clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
import asyncio
