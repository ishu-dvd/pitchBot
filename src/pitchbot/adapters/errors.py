class AdapterError(RuntimeError):
    pass


class TransientAdapterError(AdapterError):
    pass


class PermanentAdapterError(AdapterError):
    pass


class AdapterTimeoutError(TransientAdapterError):
    pass


class CircuitOpenError(TransientAdapterError):
    pass


class ExternalNetworkDisabledError(PermanentAdapterError):
    pass


class IdempotencyConflictError(PermanentAdapterError):
    pass


class MockCapacityError(PermanentAdapterError):
    pass
