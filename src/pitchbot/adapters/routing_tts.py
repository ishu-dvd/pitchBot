"""Sending each language to the engine that can actually serve it.

Until now PitchBot had exactly one synthesiser, and every language it spoke had to come
from that one engine. That is why Hindi has never been speakable: **every** published Piper
Hindi voice reviewed on 2026-09-03 is CC-BY-NC-SA or points at an unresolvable licence, so
a sales assistant may map `hi` to nothing at all. English and Telugu are cleared; Hindi is
a hole that no amount of choosing between Piper voices can fill.

The hole is structural rather than a missing voice file, so the fix is structural: let a
deployment name a different engine for the languages its main engine cannot serve.

Deliberately not a fallback chain. A route is a **decision**, and an unrouted language is
an error rather than something to guess at, for the same reason ``PiperVoiceRegistry``
refuses an unmapped language: a language served by the wrong engine does not fail, it
produces confident audio in the wrong voice or the wrong language, which is worse than
silence and much harder to notice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from pitchbot.adapters.contracts import (
    Preloadable,
    SynthesizedAudioChunk,
    TextToSpeechAdapter,
)
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode


class LanguageRoutedTextToSpeech(TextToSpeechAdapter):
    """One synthesiser per language, with a default for everything else."""

    def __init__(
        self,
        default: TextToSpeechAdapter,
        routes: Mapping[LanguageCode, TextToSpeechAdapter],
    ) -> None:
        if not routes:
            raise ValueError(
                "LanguageRoutedTextToSpeech needs at least one route; with none it is just "
                "the default adapter wearing a disguise"
            )
        self._default = default
        self._routes = dict(routes)

    @property
    def routed_languages(self) -> frozenset[LanguageCode]:
        return frozenset(self._routes)

    def adapter_for(self, language: LanguageCode) -> TextToSpeechAdapter:
        return self._routes.get(language, self._default)

    async def preload(self) -> None:
        """Forward preload to every engine behind this router.

        A wrapper that forwards ``synthesize`` and nothing else does not merely fail to add
        a capability - it **removes** one. ``preload_speech_providers`` decides by
        ``isinstance(provider, Preloadable)`` on whatever ``build_text_to_speech`` returned,
        and that is this object as soon as one language is routed. Without this method the
        check was ``False``, so configuring Hindi silently stopped preloading **Piper**, and
        its 2,561 ms voice load moved back into the first English or Telugu turn.

        Both engines need it and neither is cheap: loading Piper stalls the loop ~2 s and
        loading Supertonic was measured at 1,358 ms with a worst-case 489 ms of loop
        lateness. Synthesis through either, once resident, does not stall it at all.

        Engines are visited default-first then by language name, and each is visited once -
        ``_supertonic_routes`` deliberately hands the *same* adapter to every language it
        serves, so ``hi`` and ``mixed`` are one object, not two.

        Failures propagate. A missing model or a denied licence must stop the server, which
        is the entire reason providers are built eagerly.
        """

        engines = [self._default]
        engines.extend(self._routes[language] for language in sorted(self._routes, key=str))
        seen: set[int] = set()
        for engine in engines:
            if id(engine) in seen:
                continue
            seen.add(id(engine))
            if isinstance(engine, Preloadable):
                await engine.preload()

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        adapter = self.adapter_for(language)
        try:
            stream = adapter.synthesize(text, language)
        except Exception as error:  # noqa: BLE001 - re-raised with the route named
            raise PermanentAdapterError(
                f"routed synthesiser for {language.value!r} failed to start: {error}"
            ) from error
        async for chunk in stream:
            yield chunk


__all__ = ["LanguageRoutedTextToSpeech"]
