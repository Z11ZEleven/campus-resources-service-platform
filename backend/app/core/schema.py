from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.core.time import as_utc

Id = Annotated[
    int,
    Field(gt=0, le=18446744073709551615),
    PlainSerializer(lambda value: str(value), return_type=str),
]
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
