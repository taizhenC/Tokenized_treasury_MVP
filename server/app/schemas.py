"""Request bodies. Pydantic checks them before any SQL runs; a failure becomes a 400.

strict=True: a whole number must be a JSON integer - 5.0, "5" and true are all rejected.
extra="forbid": unknown fields are rejected, so a body can never name the account to act for.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _no_nul(value: str) -> str:
    # PostgreSQL text cannot hold the NUL character; psycopg would raise DataError (a 500).
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


Quantity = Annotated[int, Field(ge=1, le=1_000_000)]
AccountId = Annotated[int, Field(ge=1, le=2_147_483_647)]  # the INTEGER column range
# Whole cents, 1 cent .. $100 billion: the CHECKs on operations and delivery_instructions use the same range.
AmountCents = Annotated[int, Field(ge=1, le=10_000_000_000_000)]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_no_nul)]
Text = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_no_nul)]


class StrictBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class LoginRequest(StrictBody):
    username: Text
    password: Text


class ConversionRequest(StrictBody):
    direction: Literal["TO_TOKENIZED", "TO_TRADITIONAL"]
    quantity: Quantity
    idempotency_key: IdempotencyKey = Field(alias="idempotencyKey")


class TransferRequest(StrictBody):
    to_account_id: AccountId = Field(alias="toAccountId")
    quantity: Quantity
    idempotency_key: IdempotencyKey = Field(alias="idempotencyKey")


class DeliverOrderRequest(StrictBody):
    """A valued delivery: `quantity` units to `toAccountId`, against `amountCents` (DVP)."""

    to_account_id: AccountId = Field(alias="toAccountId")
    quantity: Quantity
    amount_cents: AmountCents = Field(alias="amountCents")
    idempotency_key: IdempotencyKey = Field(alias="idempotencyKey")


class SettlementPaymentRequest(StrictBody):
    """A settlement progress payment: Fed funds the participant wires to DTC to reduce its debit."""

    amount_cents: AmountCents = Field(alias="amountCents")
    idempotency_key: IdempotencyKey = Field(alias="idempotencyKey")


class SettlementDayRequest(StrictBody):
    """The operator's end-of-day steps take no input but the key: the day comes from the database."""

    idempotency_key: IdempotencyKey = Field(alias="idempotencyKey")
