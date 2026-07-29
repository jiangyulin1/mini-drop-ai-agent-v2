import queue
import threading

from server.app.event_bus import EventBus


def test_event_bus_supports_cross_thread_publish_and_blocking_read():
    bus = EventBus()
    subscriber = bus.subscribe()

    def publish():
        bus.publish("task_changed", {"task_id": "task_1"})

    thread = threading.Thread(target=publish)
    thread.start()
    thread.join(timeout=1)

    event = subscriber.get(timeout=1)

    assert event["event"] == "task_changed"
    assert event["data"]["task_id"] == "task_1"
    assert event["id"] == "1"
    assert event["timestamp"]


def test_event_bus_unsubscribe_stops_delivery():
    bus = EventBus()
    subscriber = bus.subscribe()
    bus.unsubscribe(subscriber)

    bus.publish("task_changed", {"task_id": "task_1"})

    try:
        subscriber.get(timeout=0.05)
    except queue.Empty:
        return
    raise AssertionError("unsubscribed queue should not receive events")


def test_event_bus_history_resumes_after_event_cursor():
    bus = EventBus()
    bus.publish("task_changed", {"task_id": "task_1"})
    first = bus.get_history()[0]
    bus.publish("task_changed", {"task_id": "task_2"})

    resumed = bus.get_history(first["id"])

    assert [event["data"]["task_id"] for event in resumed] == ["task_2"]
