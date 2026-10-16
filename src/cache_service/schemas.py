"""The API and CLI share the same strict payload contract."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

InputString = Annotated[str, StringConstraints(strict=True, max_length=4096)]
InputList = Annotated[list[InputString], Field(max_length=1000)]


class PayloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    list_1: InputList
    list_2: InputList

    @model_validator(mode="after")
    def equal_lengths(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    id: str
    created: bool


class PayloadOutput(BaseModel):
    output: str
