"""The ``cancel`` command handler and its convenience runner.

A pure document transition to ``cancelled``, legal only pre-receiving
(``expected``/``arrived``) — a receipt reserves nothing and no stock has landed,
so cancel touches no stock. From ``receiving`` onward stock has physically arrived;
unwinding is a restock via the Receipt/RMA path, not an inline cancel (design spec
§2). ``assert_legal`` enforces the boundary.
"""

from __future__ import annotations

from quartermaster.application.commands import CancelReceiptCommand
from quartermaster.application.envelope import execute
from quartermaster.application.errors import OccConflict
from quartermaster.application.ports import UnitOfWork, UnitOfWorkFactory
from quartermaster.application.results import CancelReceiptResult
from quartermaster.domain.errors import ReceiptNotFound
from quartermaster.domain.ids import IdempotencyKey, ReceiptId
from quartermaster.domain.state_machines import RECEIPT_MACHINE, ReceiptState


async def cancel_receipt(uow: UnitOfWork, command: CancelReceiptCommand) -> CancelReceiptResult:
    """Cancel a pre-receiving receipt (``expected``/``arrived`` -> ``cancelled``)."""
    receipt = await uow.receipts.get(command.receipt_id)
    if receipt is None:
        raise ReceiptNotFound(f"receipt {command.receipt_id} does not exist")
    RECEIPT_MACHINE.assert_legal(receipt.state, ReceiptState.CANCELLED)
    if not await uow.receipts.cas_state(
        command.receipt_id, receipt.state, receipt.version, ReceiptState.CANCELLED
    ):
        raise OccConflict(f"receipt {command.receipt_id} changed under cancel")
    return CancelReceiptResult(receipt_id=command.receipt_id, state=ReceiptState.CANCELLED)


async def run_cancel_receipt(
    uow_factory: UnitOfWorkFactory, receipt_id: ReceiptId, key: IdempotencyKey
) -> CancelReceiptResult:
    """Build the command and run it through the envelope."""
    command = CancelReceiptCommand(receipt_id, key)

    async def handler(uow: UnitOfWork, cmd: CancelReceiptCommand) -> CancelReceiptResult:
        return await cancel_receipt(uow, cmd)

    return await execute(uow_factory, command, handler, CancelReceiptResult.decode)
