from aiogram.fsm.state import State, StatesGroup


class TicketForm(StatesGroup):
    category    = State()
    description = State()
    priority    = State()
    photo       = State()
    phone       = State()
    confirm     = State()


class FollowupForm(StatesGroup):
    reply = State()
