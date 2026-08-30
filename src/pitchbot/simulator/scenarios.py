from __future__ import annotations

from dataclasses import dataclass

from pitchbot.domain import LanguageCode


@dataclass(frozen=True, slots=True)
class ScenarioTurn:
    speaker: str
    text: str
    language: LanguageCode


SCENARIOS: dict[str, tuple[ScenarioTurn, ...]] = {
    "english-discovery": (
        ScenarioTurn(
            "buyer", "We sell apparel and need a simple online catalog.", LanguageCode.ENGLISH
        ),
        ScenarioTurn(
            "assistant",
            "Thanks. This replay demonstrates discovery context only; it does not classify intent.",
            LanguageCode.ENGLISH,
        ),
    ),
    "hindi-callback": (
        ScenarioTurn("buyer", "अभी समय नहीं है, बाद में बात करते हैं।", LanguageCode.HINDI),
        ScenarioTurn(
            "assistant",
            "ज़रूर। यह केवल कॉलबैक प्रीव्यू है; कोई वास्तविक कॉल शेड्यूल नहीं हुई।",
            LanguageCode.HINDI,
        ),
    ),
    "hinglish-preview": (
        ScenarioTurn("buyer", "Sample aur timeline ka preview dikhaiye.", LanguageCode.MIXED),
        ScenarioTurn(
            "assistant",
            "Sure, yeh simulator sirf preview dikhayega; koi message send nahi hoga.",
            LanguageCode.MIXED,
        ),
    ),
}
