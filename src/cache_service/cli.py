"""Create and retrieve payloads through HTTP; stdout contains JSON only."""

import json
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Self

import httpx
from pydantic import AliasChoices, Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from cache_service.schemas import PayloadCreated, PayloadInput, PayloadOutput


class CliSettings(BaseSettings):
    """Generate a payload and read it back, repeating the same request if requested."""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        cli_prog_name="cache-cli",
        cli_exit_on_error=False,
        cli_hide_none_type=True,
        populate_by_name=True,
    )

    host: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:8000"),
        validation_alias=AliasChoices("host", "H"),
        description="Server URL",
    )
    repeat: int = Field(default=1, ge=1, le=10000, validation_alias=AliasChoices("repeat", "r"))
    input_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("input", "i"),
        description="UTF-8 JSON file, or - for stdin (default)",
    )
    json_input: str | None = Field(
        default=None,
        validation_alias=AliasChoices("json", "j"),
        description="Inline JSON; cannot be combined with --input",
    )
    output: str = Field(
        default="-",
        validation_alias=AliasChoices("output", "o"),
        description="Output JSON Lines file, or - for stdout",
    )
    timeout: float = Field(default=30, gt=0, le=300, description="HTTP timeout in seconds")

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # This tool takes explicit command-line input; ambient HOST/INPUT variables
        # must not silently change the server or the selected source.
        return (init_settings,)

    @field_validator("host")
    @classmethod
    def clean_host(cls, value: HttpUrl) -> HttpUrl:
        if value.query or value.fragment or value.username or value.password:
            raise ValueError("host must not include credentials, a query or a fragment")
        return value

    @model_validator(mode="after")
    def exclusive_input(self) -> Self:
        if self.input_file is not None and self.json_input is not None:
            raise ValueError("use either --input or --json, not both")
        return self


def load_payload(settings: CliSettings) -> PayloadInput:
    if settings.json_input is not None:
        raw = settings.json_input
    elif settings.input_file in (None, "-"):
        raw = sys.stdin.read()
    else:
        raw = Path(settings.input_file).read_text(encoding="utf-8")
    return PayloadInput.model_validate_json(raw)


def run(settings: CliSettings) -> None:
    payload = load_payload(settings)
    with (
        ExitStack() as stack,
        httpx.Client(
            base_url=str(settings.host).rstrip("/") + "/", timeout=settings.timeout
        ) as client,
    ):
        output = (
            sys.stdout
            if settings.output == "-"
            else stack.enter_context(
                Path(settings.output).open("w", encoding="utf-8", newline="\n")
            )
        )
        for _ in range(settings.repeat):
            created_response = client.post("payload", json=payload.model_dump())
            created_response.raise_for_status()
            created = PayloadCreated.model_validate(created_response.json())
            read_response = client.get(f"payload/{created.id}")
            read_response.raise_for_status()
            result = PayloadOutput.model_validate(read_response.json())
            output.write(json.dumps(result.model_dump(), ensure_ascii=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        settings = CliSettings(_cli_parse_args=list(argv) if argv is not None else sys.argv[1:])
        run(settings)
    except (SettingsError, ValidationError) as exc:
        print(f"cache-cli: invalid input: {exc}", file=sys.stderr)
        return 2
    except (OSError, httpx.HTTPError, ValueError) as exc:
        print(f"cache-cli: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
