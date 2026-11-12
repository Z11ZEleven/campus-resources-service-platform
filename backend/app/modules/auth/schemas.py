from pydantic import Field

from app.core.schema import Id, Input, Output


class Login(Input):
    username: str = Field(min_length=1, max_length=64)
    # Password whitespace must be preserved; strip only username in login service.
    password: str = Field(min_length=1, max_length=256)

    model_config = {"extra": "forbid", "str_strip_whitespace": False}


class UserView(Output):
    id: Id
    username: str
    display_name: str
    identity_type: str
    department_code: str
    roles: list[str]
    identity_id: Id
    permissions: list[str]


class IdentitySelection(Input):
    identity_id: Id


class RoleGrantInput(Input):
    role_code: str = Field(pattern="^(USER|ROOM_ADMIN|PROPERTY_ADMIN|REPAIRER|SYS_ADMIN)$")
    scope_type: str = Field(default="ROOM", pattern="^(GLOBAL|ROOM|DEPARTMENT|BUILDING)$")
    scope_value: str = Field(min_length=1, max_length=64)
