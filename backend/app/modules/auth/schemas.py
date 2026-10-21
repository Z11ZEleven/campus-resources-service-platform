from pydantic import Field

from app.core.schema import Id, Input, Output


class Login(Input):
    username: str = Field(min_length=1, max_length=64)
    # Password whitespace must be preserved; strip only username in login service.
    password: str = Field(min_length=1, max_length=256)

    model_config = {"extra": "forbid", "str_strip_whitespace": False}


class Token(Output):
    access_token: str
    token_type: str = "bearer"


class UserView(Output):
    id: Id
    username: str
    display_name: str
    identity_type: str
    department_code: str
    roles: list[str]
