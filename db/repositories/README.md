# db/repositories

Narrow read helpers so services do not each grow their own copy of the same query.

The package re-exports snapshot lookup from `services.ledger`, journal assembly from `journal`, and the research library from `research`. New query helpers belong here when more than one service needs them.
