from dataclasses import dataclass


@dataclass
class User:
    id: int
    email: str
    preferred_language: str = "en"


@dataclass
class Order:
    user_id: int
    total: float
