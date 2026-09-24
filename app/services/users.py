from app.domain.models import User
from app.services.common import new_id, require
from app.storage.base import UserRepository


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    def register(self, name: str, phone: str) -> User:
        require(bool(name.strip()) and bool(phone.strip()), "name and phone are required")
        return self.users.add(User(new_id("U"), name.strip(), phone.strip()))
