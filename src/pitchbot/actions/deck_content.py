"""What a deck says, in each language PitchBot sells in.

Kept beside the deck rather than borrowed from :mod:`pitchbot.conversation.planning` for
two reasons. The layering one: ``actions`` imports ``domain`` and ``adapters`` and never
``conversation``. The editorial one: a spoken pitch is a sentence with a *because* in it,
and a slide bullet is a noun phrase. Reusing the spoken lines would produce a deck that
reads like a transcript.

Every language with a table answers in itself. ``UNKNOWN`` gets English, matching
:func:`pitchbot.conversation.planning._table` - guessing an Indic language for a buyer
nobody could identify is a worse failure than being formal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pitchbot.domain import LanguageCode, business_types
from pitchbot.domain import features as catalog_features

_INDUSTRIES: Final[frozenset[str]] = frozenset(business_types())
_FEATURES: Final[frozenset[str]] = frozenset(catalog_features())


@dataclass(frozen=True, slots=True)
class DeckPhrases:
    """One language's deck copy.

    Validated on construction so a language cannot ship a deck with a missing industry or
    feature. A deck is handed to a buyer, so a ``KeyError`` at render time would be a
    defect in front of the customer.
    """

    title_template: str
    heard_title: str
    opportunity_title: str
    scope_title: str
    next_step_title: str
    business_label: str
    features_label: str
    budget_label: str
    timeline_label: str
    unstated: str
    industry_name: Mapping[str, str]
    industry_bullets: Mapping[str, tuple[str, ...]]
    feature_label: Mapping[str, str]
    next_steps: tuple[str, ...]

    def __post_init__(self) -> None:
        if set(self.industry_name) != _INDUSTRIES:
            raise ValueError("Deck industry names must cover exactly the catalogue")
        if set(self.industry_bullets) != _INDUSTRIES:
            raise ValueError("Deck industry bullets must cover exactly the catalogue")
        if set(self.feature_label) != _FEATURES:
            raise ValueError("Deck feature labels must cover exactly the catalogue")
        if "{business}" not in self.title_template:
            raise ValueError("Deck title template must place the business")
        if not self.next_steps:
            raise ValueError("Deck next steps must not be empty")


_PHRASES: Final[Mapping[LanguageCode, DeckPhrases]] = {
    LanguageCode.ENGLISH: DeckPhrases(
        title_template="{business}: proposed website scope",
        heard_title="What you told us",
        opportunity_title="Where the sales come from",
        scope_title="What we would build",
        next_step_title="What it takes to start",
        business_label="Business",
        features_label="Asked for",
        budget_label="Budget",
        timeline_label="Timeline",
        unstated="not discussed yet",
        industry_name={
            "apparel": "Clothing store",
            "toys": "Toy store",
            "books": "Bookshop",
            "food": "Food business",
            "import-export": "Import-export business",
            "plastics": "Plastics manufacturing",
        },
        industry_bullets={
            "apparel": (
                "Size and colour variants on one product page",
                "Seasonal collections that can be swapped without a rebuild",
                "Mobile-first photography, because that is where buyers decide",
            ),
            "toys": (
                "Discovery by age and category",
                "Safety and compliance information beside each item",
                "Gift-ready collections for festival demand",
            ),
            "books": (
                "Search that matches how readers already think",
                "Author and genre browsing",
                "New-release collections kept current",
            ),
            "food": (
                "Menu with live availability",
                "Delivery areas and timings stated up front",
                "Dietary and allergen information",
            ),
            "import-export": (
                "Product specifications buyers can compare",
                "Markets served and certifications held",
                "Inquiry workflow that filters serious buyers",
            ),
            "plastics": (
                "Material and grade catalogue",
                "Technical specifications on every item",
                "Bulk inquiry workflow with quantity capture",
            ),
        },
        feature_label={
            "catalog": "Structured product catalogue",
            "online-payments": "Reviewed online payment flow",
            "inventory": "Inventory visibility",
            "whatsapp": "Policy-approved WhatsApp inquiry path",
            "multilingual": "Content in more than one language",
        },
        next_steps=(
            "Confirm requirements and who owns the content",
            "Review a synthetic prototype",
            "Approve scope before implementation",
        ),
    ),
    LanguageCode.HINDI: DeckPhrases(
        title_template="{business}: प्रस्तावित वेबसाइट दायरा",
        heard_title="आपने जो बताया",
        opportunity_title="बिक्री कहाँ से आती है",
        scope_title="हम क्या बनाएँगे",
        next_step_title="शुरू करने के लिए क्या चाहिए",
        business_label="व्यवसाय",
        features_label="आपकी ज़रूरत",
        budget_label="बजट",
        timeline_label="समय",
        unstated="अभी बात नहीं हुई",
        industry_name={
            "apparel": "कपड़ों की दुकान",
            "toys": "खिलौनों की दुकान",
            "books": "किताबों की दुकान",
            "food": "खाने का व्यवसाय",
            "import-export": "आयात-निर्यात व्यवसाय",
            "plastics": "प्लास्टिक निर्माण",
        },
        industry_bullets={
            "apparel": (
                "एक ही पेज पर साइज़ और रंग के विकल्प",
                "मौसमी कलेक्शन, जिन्हें दोबारा बनाए बिना बदला जा सके",
                "मोबाइल पर अच्छी तस्वीरें, क्योंकि ग्राहक वहीं तय करता है",
            ),
            "toys": (
                "उम्र और श्रेणी से खोज",
                "हर सामान के साथ सुरक्षा जानकारी",
                "त्योहारों के लिए गिफ़्ट कलेक्शन",
            ),
            "books": (
                "ऐसी सर्च जो ग्राहक की सोच से मेल खाए",
                "लेखक और श्रेणी से ब्राउज़िंग",
                "नई किताबों का अपडेट कलेक्शन",
            ),
            "food": (
                "मेन्यू, जिसमें उपलब्धता तुरंत दिखे",
                "डिलीवरी क्षेत्र और समय पहले से साफ़",
                "आहार और एलर्जी की जानकारी",
            ),
            "import-export": (
                "स्पेसिफिकेशन जिनकी तुलना ग्राहक कर सके",
                "किन बाज़ारों में काम और कौन से प्रमाणपत्र",
                "पूछताछ प्रक्रिया जो गंभीर ग्राहक छाँटे",
            ),
            "plastics": (
                "सामग्री और ग्रेड का कैटलॉग",
                "हर उत्पाद पर तकनीकी जानकारी",
                "थोक पूछताछ, मात्रा सहित",
            ),
        },
        feature_label={
            "catalog": "व्यवस्थित प्रोडक्ट कैटलॉग",
            "online-payments": "जाँचा हुआ ऑनलाइन भुगतान",
            "inventory": "स्टॉक की जानकारी",
            "whatsapp": "नीति के अनुसार व्हाट्सऐप पूछताछ",
            "multilingual": "एक से ज़्यादा भाषाओं में सामग्री",
        },
        next_steps=(
            "ज़रूरतें और सामग्री की ज़िम्मेदारी तय करना",
            "एक नमूना प्रोटोटाइप देखना",
            "काम शुरू करने से पहले दायरा मंज़ूर करना",
        ),
    ),
    LanguageCode.TELUGU: DeckPhrases(
        title_template="{business}: ప్రతిపాదిత వెబ్‌సైట్ పరిధి",
        heard_title="మీరు చెప్పినది",
        opportunity_title="అమ్మకాలు ఎక్కడి నుంచి వస్తాయి",
        scope_title="మేము ఏమి నిర్మిస్తాము",
        next_step_title="ప్రారంభించడానికి ఏమి కావాలి",
        business_label="వ్యాపారం",
        features_label="మీకు కావలసినవి",
        budget_label="బడ్జెట్",
        timeline_label="సమయం",
        unstated="ఇంకా చర్చించలేదు",
        industry_name={
            "apparel": "దుస్తుల దుకాణం",
            "toys": "బొమ్మల దుకాణం",
            "books": "పుస్తకాల దుకాణం",
            "food": "ఆహార వ్యాపారం",
            "import-export": "దిగుమతి-ఎగుమతి వ్యాపారం",
            "plastics": "ప్లాస్టిక్ తయారీ",
        },
        industry_bullets={
            "apparel": (
                "ఒకే పేజీలో సైజు, రంగు ఎంపికలు",
                "సీజన్ కలెక్షన్లు, మళ్లీ నిర్మించకుండా మార్చేలా",
                "మొబైల్ ఫోటోలు, కస్టమర్ అక్కడే నిర్ణయిస్తాడు కాబట్టి",
            ),
            "toys": (
                "వయసు, విభాగం ఆధారంగా వెతుకులాట",
                "ప్రతి వస్తువుతో భద్రత సమాచారం",
                "పండుగలకు గిఫ్ట్ కలెక్షన్లు",
            ),
            "books": (
                "పాఠకుల ఆలోచనకు తగిన సెర్చ్",
                "రచయిత, ప్రక్రియ ఆధారంగా బ్రౌజింగ్",
                "కొత్త పుస్తకాల తాజా జాబితా",
            ),
            "food": (
                "అందుబాటు కనిపించే మెనూ",
                "డెలివరీ ప్రాంతాలు, సమయాలు ముందే స్పష్టంగా",
                "ఆహార, అలర్జీ సమాచారం",
            ),
            "import-export": (
                "కస్టమర్ పోల్చగలిగే స్పెసిఫికేషన్లు",
                "సేవలందించే మార్కెట్లు, ధ్రువీకరణలు",
                "నిజమైన కొనుగోలుదారులను వడపోసే విచారణ",
            ),
            "plastics": (
                "మెటీరియల్, గ్రేడ్ కేటలాగ్",
                "ప్రతి వస్తువుపై సాంకేతిక వివరాలు",
                "పరిమాణంతో సహా బల్క్ విచారణ",
            ),
        },
        feature_label={
            "catalog": "క్రమబద్ధమైన ప్రొడక్ట్ కేటలాగ్",
            "online-payments": "సమీక్షించిన ఆన్‌లైన్ చెల్లింపు",
            "inventory": "ఇన్వెంటరీ కనిపించడం",
            "whatsapp": "నిబంధనల ప్రకారం వాట్సాప్ విచారణ",
            "multilingual": "ఒకటి కంటే ఎక్కువ భాషల్లో సమాచారం",
        },
        next_steps=(
            "అవసరాలు, కంటెంట్ బాధ్యత ఖరారు చేయడం",
            "ఒక నమూనా ప్రోటోటైప్ చూడటం",
            "పని మొదలుపెట్టే ముందు పరిధిని ఆమోదించడం",
        ),
    ),
    LanguageCode.MIXED: DeckPhrases(
        title_template="{business}: proposed website scope",
        heard_title="Aapne jo bataya",
        opportunity_title="Sales kahan se aati hai",
        scope_title="Hum kya banayenge",
        next_step_title="Shuru karne ke liye kya chahiye",
        business_label="Business",
        features_label="Aapki zaroorat",
        budget_label="Budget",
        timeline_label="Timeline",
        unstated="abhi baat nahi hui",
        industry_name={
            "apparel": "Kapdon ki dukaan",
            "toys": "Khilaunon ki dukaan",
            "books": "Kitaabon ki dukaan",
            "food": "Food business",
            "import-export": "Import-export business",
            "plastics": "Plastics manufacturing",
        },
        industry_bullets={
            "apparel": (
                "Ek hi product page par size aur colour options",
                "Seasonal collections, dobara banaye bina badal sakein",
                "Mobile-first photos, kyunki customer wahin decide karta hai",
            ),
            "toys": (
                "Age aur category se discovery",
                "Har item ke saath safety information",
                "Tyohaaron ke liye gift-ready collections",
            ),
            "books": (
                "Search jo reader ki soch se match kare",
                "Author aur genre se browsing",
                "Nayi releases ka updated collection",
            ),
            "food": (
                "Menu jisme availability turant dikhe",
                "Delivery area aur timing pehle se saaf",
                "Dietary aur allergen information",
            ),
            "import-export": (
                "Specifications jinki comparison ho sake",
                "Kaunse markets aur kaunse certifications",
                "Inquiry workflow jo serious buyers chhaante",
            ),
            "plastics": (
                "Material aur grade ka catalogue",
                "Har item par technical specifications",
                "Bulk inquiry, quantity ke saath",
            ),
        },
        feature_label={
            "catalog": "Structured product catalogue",
            "online-payments": "Reviewed online payment flow",
            "inventory": "Stock ki visibility",
            "whatsapp": "Policy ke hisaab se WhatsApp inquiry",
            "multilingual": "Ek se zyada bhaashaon mein content",
        },
        next_steps=(
            "Requirements aur content ki zimmedari tay karna",
            "Ek synthetic prototype dekhna",
            "Kaam shuru karne se pehle scope approve karna",
        ),
    ),
}


def deck_languages() -> frozenset[LanguageCode]:
    """Languages a deck can actually be written in, as opposed to ones the enum names."""

    return frozenset(_PHRASES)


def phrases_for(language: LanguageCode) -> DeckPhrases:
    """Which deck copy answers this language, falling back to English for ``UNKNOWN``."""

    return _PHRASES[language if language in _PHRASES else LanguageCode.ENGLISH]


__all__ = ["DeckPhrases", "deck_languages", "phrases_for"]
