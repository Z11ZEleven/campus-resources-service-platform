from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from app.core.time import as_utc

Id = Annotated[
    int,
    Field(gt=0, le=18446744073709551615),
    PlainSerializer(lambda value: str(value), return_type=str),
]


def decimal_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("ID 必须是正整数或十进制字符串")
    if isinstance(value, str) and (not value.isascii() or not value.isdecimal()):
        raise ValueError("ID 必须是正整数或十进制字符串")
    return value


DecimalId = Annotated[Id, BeforeValidator(decimal_id)]
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(
        lambda value: as_utc(value).isoformat().replace("+00:00", "Z"), return_type=str
    ),
]


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
