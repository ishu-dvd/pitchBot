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


class UnsupportedLanguageError(RuntimeError):
    """The buyer's language is one this transcriber is known not to be able to transcribe.

    Not an :class:`AdapterError`, for the same reason :class:`DeliberationPreempted` is not:
    nothing failed. The adapter identified the language, recognised it as one it cannot
    serve, and declined before spending the CPU - which is a correct outcome and must not be
    logged as a fault or reported to the caller as "the transcriber is unavailable", because
    the transcriber is available and working.

    Measured 2026-09-05: Whisper ``small`` on Telugu returns nonsense in every decoder
    configuration tried and takes 37,533 ms to do it. Declining in ~1.7 s and saying which
    language was heard is strictly better than either outcome.
    """

    def __init__(self, language: str) -> None:
        super().__init__(
            f"this transcriber cannot transcribe {language!r}; the utterance was declined "
            "rather than transcribed into text that cannot be trusted"
        )
        self.language = language
