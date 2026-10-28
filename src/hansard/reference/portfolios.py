"""Map role titles, committee names and free text to policy portfolios.

parlparse's post data (from data.parliament.uk / MNIS) carries no structured
department: every post has ``organization_id: "house-of-commons"`` and the
department lives only in the free-text role, e.g.

* "Parliamentary Under-Secretary (Department for Transport)"
* "Foreign Secretary, Foreign, Commonwealth and Development Office"
* "Shadow Minister (Transport)"

A *portfolio* is a stable policy area that survives machinery-of-government
changes (DECC -> BEIS -> DESNZ all touch ``energy_climate``), so a speech by the
Energy Secretary in 2012 and in 2024 both match an energy question.

The same rules classify committee names and, with a few extra topic keywords,
the text of a user's question (an offline fallback for the LLM query parser).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PORTFOLIOS: dict[str, str] = {
    "pm": "Prime Minister",
    "cabinet_office": "Cabinet Office, constitution and the civil service",
    "treasury": "HM Treasury: economy, tax and public spending",
    "home_office": "Home Office: policing, crime, immigration and security",
    "foreign": "Foreign, Commonwealth and Development Office (FCO before 2020)",
    "international_development": "International development (DFID until 2020)",
    "defence": "Defence, armed forces and veterans",
    "justice": "Justice: courts, prisons, probation and sentencing",
    "law_officers": "Law Officers: Attorney General, Solicitor General, Advocate General",
    "health": "Health and social care",
    "education": "Education: schools, children, skills and universities",
    "work_pensions": "Work, pensions, welfare and employment",
    "transport": "Transport: rail (incl. HS2), roads, buses and aviation",
    "energy_climate": "Energy, climate change and net zero",
    "business": "Business, industry and enterprise",
    "trade": "International trade and exports",
    "science_tech": "Science, innovation, technology and digital",
    "environment": "Environment, food, farming and rural affairs",
    "housing_communities": "Housing, planning, communities and local government",
    "culture": "Culture, media and sport",
    "northern_ireland": "Northern Ireland",
    "scotland": "Scotland",
    "wales": "Wales",
    "exiting_eu": "Exiting the EU and EU relations",
    "equalities": "Women and equalities",
    "house_business": "Leaders of the House (parliamentary business)",
}

_I = re.IGNORECASE

# (portfolio, pattern). All matching portfolios are collected, so joint posts
# ("... (Jointly with the Department for Education)") map to several.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("pm", re.compile(r"(?<!deputy )\bprime minister\b(?!['’]s policy)|leader of hm official opposition", _I)),
    ("cabinet_office", re.compile(
        r"cabinet office|duchy of lancaster|minister without portfolio|deputy prime minister"
        r"|first secretary of state|paymaster general|government policy|constitution|civil service"
        r"|privy council|lord privy seal|intergovernmental|deputy priministerial|cop26|number 10"
        r"|policy unit|devolved issues|public administration|public sector reform|government efficiency"
        r"|democratic standards|political and constitutional reform|policy renewal|devolved government"
        r"|lord president|national resilience|common frameworks|citizenship and civic", _I)),
    # Whip titles that mention the Treasury are classified as whips before this runs.
    ("treasury", re.compile(
        r"treasury|exchequer|economic secretary|commercial secretary|\(city\)|economic affairs"
        r"|public accounts|financial secretary|\beconomy\b|financial services|financial exclusion|finance bill|banking|tax law|economic and financial", _I)),
    ("home_office", re.compile(
        r"home office|home department|home affairs|\(home\)|policing|immigration|(?<!youth )crime"
        r"|terrorism|borders|\bfire\b|migration|\(identity\)|domestic violence|safeguarding"
        r"|\(security\)|security and immigration|national security|victims and abuse|home secretary"
        r"|civil liberties|investigatory powers|domestic abuse|modern slavery|extradition|licensing act", _I)),
    ("foreign", re.compile(
        r"foreign|commonwealth(?! games)|\(europe\)|minister for europe|middle east|africa|\(africa and un\)"
        r"|international energy policy|afghan|international relations|european union committee"
        r"|european scrutiny|eu .*sub-committee|international affairs|international human rights|european affairs|external affairs|soft power|sexual violence in conflict|international agreements|arms export", _I)),
    ("international_development", re.compile(
        r"international development|\(development\)|minister for development|development affairs"
        r"|development office|minister of state \(development\)", _I)),
    ("defence", re.compile(r"defence|armed forces|veterans|national security|arms export", _I)),
    ("justice", re.compile(
        r"(?<!climate )justice|lord chancellor|prisons|probation|sentencing|courts|\bvictims\b"
        r"|(?<!international )human rights|civil liberties|prison|bribery|mental capacity|end of life|extradition"
        r"|constitutional affairs", _I)),
    ("law_officers", re.compile(r"attorney|solicitor[- ]general|advocate[- ]general", _I)),
    ("health", re.compile(
        r"health|social care|care and older people|dentistry|public health|primary care|hospitals"
        r"|\bcancer\b|care and carers|\\bnhs\\b|vaccinations|community care|preterm birth|autism|mental capacity|end of life|covid", _I)),
    ("education", re.compile(
        r"education|schools|children|skills|universities|apprenticeships|early years|14-19"
        r"|further & higher|young people|childcare|social mobility|numeracy", _I)),
    ("work_pensions", re.compile(
        r"work and pensions|pensions|disabled people|\(employment\)|employment rights|future of work"
        r"|welfare|child poverty|child maintenance|family support|disabilit|carers|employment"
        r"|workers' rights|fair work|retirement|demographic change|intergenerational|social security", _I)),
    ("transport", re.compile(r"transport|\(buses\)|infrastructure|high speed rail", _I)),
    ("energy_climate", re.compile(
        r"energy|climate|net zero|clean power|decarbonisation|clean growth|green jobs|environmental audit", _I)),
    ("business", re.compile(
        r"business|enterprise|industr|regulatory reform|consumer|postal affairs|\bsteel\b"
        r"|employment relations|board of trade|trade and industry|manufacturing|trade unions", _I)),
    ("trade", re.compile(r"\btrade\b(?! unions)|exports|investment|board of trade|international agreements|arms export", _I)),
    ("science_tech", re.compile(
        r"science|innovation|technology|digital|\btech\b|internet|\bai\b|intellectual property"
        r"|research|artificial intelligence|online safety|disinformation|engagement with space", _I)),
    ("environment", re.compile(
        r"environment(?!al audit)(?! and net zero)|rural|farming|\bfood\b|marine|fisheries|flooding"
        r"|\bwater\b|\bwaste\b|nature|oceans|coastal|environmental audit|land use|horticultur|defra|agricultur", _I)),
    ("housing_communities", re.compile(
        r"communities|local government|housing|planning|levelling up|decentralisation|devolution"
        r"|northern powerhouse|\bcities\b|regional affairs|\blondon\b|land use|office of the deputy prime minister"
        r"|\bthe regions\b", _I)),
    ("culture", re.compile(
        r"(?<!agri)(?<!horti)culture|media|\bsport\b|olympics|tourism|\barts\b|heritage|creative industries|gambling"
        r"|charities|civil society|voluntary sector|third sector|youth affairs|olympic|online safety|disinformation|dcms", _I)),
    ("northern_ireland", re.compile(r"northern ireland|windsor framework", _I)),
    ("scotland", re.compile(r"scotland|scottish", _I)),
    ("wales", re.compile(r"\bwales\b|welsh", _I)),
    ("exiting_eu", re.compile(
        r"exiting the european union|brexit|eu negotiations|future relationship with the european union"
        r"|\beu accession", _I)),
    ("equalities", re.compile(r"equalit|\bwomen\b|women's|\bfaiths?\b|inclusive society", _I)),
    ("house_business", re.compile(r"leader of the house|\(house of lords\)", _I)),
]

# Extra topic words used only when classifying a user's question.
_QUERY_TOPICS: list[tuple[str, re.Pattern[str]]] = [
    ("transport", re.compile(
        r"\bhs2\b|high speed (rail|2)|railways?|\brail\b|\btrains?\b|\broads?\b|motorways?|aviation"
        r"|airports?|\bbus(es)?\b|potholes|crossrail", _I)),
    ("health", re.compile(r"\bnhs\b|hospitals?|\bgps?\b|nurses|doctors|waiting lists?|vaccin", _I)),
    ("education", re.compile(r"\bschools?\b|teachers|pupils|ofsted|tuition fees|students|universit|curriculum"
                             r"|academies|special educational needs|nurser(y|ies)|\bgcses?\b|a[- ]levels?", _I)),
    ("home_office", re.compile(r"police|asylum|small boats|knife|prisons?", _I)),
    ("treasury", re.compile(r"\btax(es|ation)?\b|budget|spending review|inflation|\bvat\b|deficit|borrowing", _I)),
    ("environment", re.compile(r"agricultur|farmers?|zoning|land use|pollution|sewage|biodiversity", _I)),
    ("housing_communities", re.compile(r"councils?|homes?\b|landlords?|renters?|planning permission", _I)),
    ("energy_climate", re.compile(r"\boil\b|\bgas\b|nuclear|wind (farms?|power)|solar|emissions", _I)),
    ("defence", re.compile(r"\bmilitary|\bnato\b|\bnavy\b|\barmy\b|\braf\b|ukraine", _I)),
    ("work_pensions", re.compile(r"universal credit|benefits?\b|unemployment|state pension", _I)),
    ("exiting_eu", re.compile(r"\beu\b|european union|single market|customs union", _I)),
]

_WHIP = re.compile(
    r"\bwhip\b|lord commissioner|lords? in waiting|baroness in waiting|hm household|gentlemen-at-arms"
    r"|yeomen of the guard|parliamentary secretary to the treasury|parliamentary secretary, hm treasury"
    r"|treasurer of hm household|vice-chamberlain|comptroller|lord chamberlain", _I)
_MINISTERIAL = re.compile(r"secretary of state|minister|under-secretary|under secretary|spokesperson", _I)
_PARTY_ROLE = re.compile(
    r"campaign|election co-ordinator|chair of labour policy review|westminster leader|party chair"
    r"|deputy leader|group leader|policy review|\bpps\b", _I)

# Ministerial titles that genuinely name no department in the source data.
UNSPECIFIED_TITLES = {
    "minister of state",
    "parliamentary under-secretary",
    "minister on leave (minister of state)",
    "minister on leave (parliamentary under secretary of state)",
    "minister on leave (secretary of state)",
    "minister on leave (attorney general)",
    "shadow minister",
    "shadow secretary of state",
    "deputy leader of the official opposition",
    "minister of state for portsmouth",
    "shadow dup spokesperson (without portfolio)",
    "shadow spokesperson",
}

KIND_GOV = "gov"
KIND_SHADOW = "shadow"
KIND_CHAIR = "chair"
KIND_MEMBER = "committee_member"
KIND_WHIP = "whip"
KIND_PARLIAMENTARY = "parliamentary"
KIND_PARTY = "party"


@dataclass(frozen=True)
class RoleClass:
    kind: str
    portfolios: tuple[str, ...]


def portfolios_in(text: str) -> tuple[str, ...]:
    """Portfolios named in a role title or committee name."""
    found = [key for key, pattern in _RULES if pattern.search(text or "")]
    return tuple(dict.fromkeys(found))


def portfolios_for_query(text: str) -> tuple[str, ...]:
    """Offline topic -> portfolio guess for a question (LLM parser fallback)."""
    found = list(portfolios_in(text))
    found += [key for key, pattern in _QUERY_TOPICS if pattern.search(text or "")]
    return tuple(dict.fromkeys(found))


def classify_post(source: str, role: str) -> RoleClass:
    """Classify a parlparse/datadotparl post (government, opposition, parliamentary)."""
    role = (role or "").strip()
    kind_src = source.rsplit("/", 1)[-1]
    if kind_src == "parliamentarypost":
        return RoleClass(KIND_PARLIAMENTARY, ())
    is_whip = bool(_WHIP.search(role)) and not _MINISTERIAL.search(
        _WHIP.sub("", role)) and "spokesperson" not in role.lower()
    if is_whip:
        return RoleClass(KIND_WHIP, ())
    if kind_src == "oppositionpost":
        found = portfolios_in(role)
        if not found and _PARTY_ROLE.search(role):
            return RoleClass(KIND_PARTY, ())
        return RoleClass(KIND_SHADOW, found)
    return RoleClass(KIND_GOV, portfolios_in(role))


def classify_committee(name: str, role: str) -> RoleClass:
    kind = KIND_CHAIR if (role or "").strip().lower() in {"chair", "chairman", "chairwoman"} else KIND_MEMBER
    return RoleClass(kind, portfolios_in(name))


def is_unspecified_title(role: str) -> bool:
    return " ".join((role or "").lower().split()) in UNSPECIFIED_TITLES
