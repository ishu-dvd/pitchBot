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


class DeliberationPreempted(RuntimeError):
    """Generation was abandoned on request, which is a decision and not a fault.

    Deliberately **not** an :class:`AdapterError`. Every fallback path in this codebase
    treats an adapter error as "the model let us down, use the rules instead", and logs it
    at warning level. Preemption is the opposite: the model was working correctly and was
    told to stop so the buyer could be answered. Classifying it as a fault would fill the
    logs with warnings during entirely normal operation and would hide a real adapter
    failure among them.
    """
