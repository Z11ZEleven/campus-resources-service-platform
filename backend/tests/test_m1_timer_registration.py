from app.modules.m1.timers import handle_timer
from app.modules.outbox.handlers import HANDLERS


def test_m1_timer_is_registered_for_the_default_worker():
    assert HANDLERS["m1_timer"] is handle_timer
