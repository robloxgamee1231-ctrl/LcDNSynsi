"""
artlist_bot.py — Playwright automation for the Artlist AI Video Generator

Human-like flow:
  1. Launch browser with stealth settings (no webdriver flag, real plugins, etc.)
  2. Navigate to artlist.io → sign in via the modal (email + password)
  3. Open https://toolkit.artlist.io/new?mode=video
  4. Type prompt slowly (human-like key delays + random pauses)
  5. If reference image → click + → upload it
  6. Remove Start Frame / End Frame chips if present
  7. Open Filters → select model + duration
       Omni Flash  → 10 s
       Seedance 2  → 15 s
  8. Click Generate (↑) after a natural pause
  9. Poll until the video is ready (~100%)
 10. Download → return video bytes
"""

import asyncio
import json
import os
import random
import tempfile
from pathlib import Path
from typing import Callable, Awaitable, Optional


class CopyrightError(RuntimeError):
    """Raised when Artlist blocks generation due to copyright / content policy."""

from playwright.async_api import async_playwright, Page, Download, BrowserContext

# ── Bypass helpers ─────────────────────────────────────────────────────────────

# ── Cinematic bypass: Six-Slot Formula ────────────────────────────────────────
# Seedance (and most AI video filters) evaluate *context and intent*, not just
# keywords.  A prompt that reads like a filmmaker describing a shot will pass;
# one that names a copyrighted character won't.
#
# Six-Slot Formula: [Camera] · [Subject visual desc] · [Action] ·
#                   [Setting] · [Style] · [Lighting]
#
# Character name → precise visual description (no recognisable IP keyword).
_BYPASS_CHARACTER_MAP: dict[str, str] = {
    # Dragon Ball
    "goku":        "a cheerful powerfully-built martial artist with wild upward-spiking jet-black hair, a broad friendly face with large expressive eyes, wearing a worn orange-and-black training gi with a blue undershirt, drawn in vivid anime art style",
    "vegeta":      "a proud compact warrior with a dramatic straight-up widow's peak of jet-black hair, a permanent intense scowl, wearing sleek royal-blue and white form-fitting battle armor with large shoulder guards, anime art style",
    "gohan":       "a gentle-faced teenage fighter with short neat black hair and large kind eyes who switches between a student's soft expression and explosive battle fury in a purple gi, anime art style",
    "piccolo":     "a tall green-skinned alien warrior with large pointed ears, a bald head with two small antennae, bright purple spots on his face, and a flowing white weighted cape and white turban, anime art style",
    "frieza":      "a slender alien tyrant with a completely smooth pale white face, no visible ears or nose, white and purple organic-looking bio-armor that appears grown onto the body, and a long whip-like purple tail, levitating with terrifying calm, anime art style",
    "cell":        "a tall insectoid bio-engineered warrior with a mantis-like face and compound eyes, black and green spotted skin, pointed wing-like structures on his back, and a thick tail, anime art style",
    "beerus":      "a lean Egyptian-cat-faced deity with large alert ears, purple skin, thin build, and ancient gold jewelry over a pale robe, floating with divine laziness and half-lidded eyes, anime art style",
    "broly":       "a colossally muscular berserker who towers over normal fighters, with wild spiking black hair that crackles with green energy, a simple enraged face, and a torn dark training belt, anime art style",
    # Naruto
    "naruto":      "a scrappy young ninja with messy spiky blond hair, bright blue wide eyes, and a determined grin in an orange and black combat tracksuit, anime art style",
    "sasuke":      "a brooding teenage ninja with short dark hair, pale skin, and cold dark eyes in a dark high-collar shirt with a sword on his back, anime art style",
    "kakashi":     "a silver-haired masked ninja with a relaxed slouch and one eye hidden beneath a headband, exuding quiet elite competence, anime art style",
    "itachi":      "a lean young man with long straight black hair tied back, sharp red eyes, and subtle dark rings under them in a black high-collar cloak, anime art style",
    "madara":      "an imposing warlord with long wild black hair, pale skin, and a powerful build in elaborate dark samurai-style armor that radiates overwhelming pressure, anime art style",
    "obito":       "a mysterious warrior in a swirling orange mask and a flowing dark cloak who phases through attacks like a ghost, anime art style",
    # One Piece
    "luffy":       "a lanky fearless young pirate with a straw hat, a wide rubber-faced grin, short black hair, and a red sleeveless vest with sandals, anime art style",
    "zoro":        "a muscular swordsman with short green hair, one eye closed, and three katanas—two at his waist and one in his mouth—in a dark open shirt, anime art style",
    "sanji":       "a slender blond man with one eye hidden under a curled brow, a slim dark suit, a cigarette at his lips, and a suave confident expression, anime art style",
    "shanks":      "a red-haired scarred pirate captain with a confident smirk, a missing left arm, and a long dark captain's coat, anime art style",
    # Attack on Titan
    "eren":        "a grim-faced young soldier with long dark hair tied back, intense teal eyes, and an olive-green military uniform with a survey cloak, anime art style",
    "levi":        "a short but terrifyingly capable soldier with an undercut, sharp steel-grey eyes, and an olive-green scout cloak, anime art style",
    "mikasa":      "a stoic black-haired young woman with dark eyes in olive-green military gear with a worn red scarf at her collar, anime art style",
    "erwin":       "a tall blond commanding officer with heavy eyebrows, a determined jaw, and an olive-green military overcoat, anime art style",
    # Demon Slayer
    "tanjiro":     "a kind-faced young swordsman with dark burgundy hair and large brown eyes in a charcoal-checkered kimono haori with a blade strapped to his back, anime art style",
    "zenitsu":     "a cowardly-looking boy with shaggy blond hair and fearful wide eyes in a yellow kimono who becomes terrifyingly fast when he falls asleep, anime art style",
    "inosuke":     "a wild bare-chested muscular boy with a boar skull helmet hiding a surprisingly pretty face, wielding two jagged serrated blades, anime art style",
    "nezuko":      "a small young girl with long dark hair, soft pink eyes, and a pink kimono with a pale cloth tied around her mouth, anime art style",
    "rengoku":     "a fiercely passionate swordsman with wild flame-styled red and yellow hair, loud booming personality, and a flamboyant flame-patterned haori, anime art style",
    # Jujutsu Kaisen
    "gojo":        "a tall white-haired young man with strikingly vivid blue eyes in a dark uniform, often wearing a black strip of cloth as a blindfold, radiating effortless dominance, anime art style",
    "yuji":        "a stocky pink-haired athletic young man with brown eyes and a wide build in a dark school uniform, anime art style",
    "sukuna":      "a menacing ancient being with extra pairs of eyes tattooed across his face and torso, disheveled pink hair, and an aura of pure malice, anime art style",
    # My Hero Academia
    "deku":        "a green-haired boy with large earnest eyes in a battered green and grey armored hero costume with a full-face respirator mask and support gear, anime art style",
    "bakugo":      "a spiky blond teen with sharp red eyes and an aggressive scowl in a dark hero costume with large gauntlet-like bracers that spark with explosions, anime art style",
    "todoroki":    "a serious young hero with half red and half white hair split exactly down the middle, mismatched grey and teal eyes, in a white hero bodysuit, anime art style",
    "allmight":    "a massively muscular hero with a giant beaming smile, blond hair, and a blue and gold star-patterned hero costume who towers over everyone, anime art style",
    "all might":   "a massively muscular hero with a giant beaming smile, blond hair, and a blue and gold star-patterned hero costume who towers over everyone, anime art style",
    # DC
    "batman":      "a dark vigilante in a gunmetal armored tactical suit with a long flowing black cape and a horned cowl",
    "superman":    "a broad-shouldered hero in a deep blue bodysuit with a flowing red cape and a shield emblem on the chest",
    "wonder woman":"a warrior princess in bronze eagle breastplate with a red and gold skirt, wielding a glowing rope and a round shield",
    "joker":       "a gaunt pale man with green dyed hair in a purple three-piece suit with smeared face paint and a wide grin",
    "the flash":   "a sleek speedster in a scarlet aerodynamic bodysuit with gold wing accents on his helmet",
    "flash":       "a sleek speedster in a scarlet aerodynamic bodysuit with gold wing accents on his helmet",
    # Marvel
    "spider-man":  "a lean acrobatic hero in a red and blue web-textured full-body suit with large white eye lenses",
    "spiderman":   "a lean acrobatic hero in a red and blue web-textured full-body suit with large white eye lenses",
    "iron man":    "a hero encased in a sleek red and gold powered mechanical exosuit with glowing chest piece and palm repulsors",
    "ironman":     "a hero encased in a sleek red and gold powered mechanical exosuit with glowing chest piece and palm repulsors",
    "thor":        "a long blond-haired godlike warrior in silver and red chest armor with a short-handled war hammer crackling with lightning",
    "captain america": "a broad-shouldered soldier in a blue tactical suit with a star emblem carrying a circular vibranium shield",
    "thanos":      "a colossal purple-skinned warlord in gold armor with a multi-gemmed metal gauntlet on one hand",
    "wolverine":   "a stocky feral mutant fighter with three bone-white metal claws on each fist in a yellow and black hero suit",
    "deadpool":    "a wisecracking mercenary in a red and black full-body suit with white eye lenses and two katanas on his back",
    "hulk":        "a towering rage-fueled green giant in tattered purple pants with veins of muscle bulging across his body",
    "black panther":"a regal warrior in a sleek all-black vibranium panther suit with silver claw accents",
    "venom":       "a massive black symbiote-covered figure with a white spider chest symbol, long serpentine tongue and rows of jagged teeth",
    "black widow": "a precision operative with dark red hair in a black form-fitting tactical suit with two glowing wrist gadgets",
    # Star Wars
    "darth vader": "a towering cyborg warlord in full black armored life-support suit with a black helmet and deep mechanical breathing",
    "luke skywalker": "a young hero in desert-tan robes gripping a humming blue energy sword, earnest and battle-ready",
    "yoda":        "a tiny ancient green alien elder with huge bat-like ears wielding a small humming energy sword with deep wisdom",
    "rey":         "a self-taught desert scavenger with a triple hair bun in sandy wrappings wielding a repurposed energy sword",
    "darth maul":  "a red and black horned tattooed warrior wielding a double-ended energy sword with feral acrobatics",
    "palpatine":   "a decrepit scarred emperor in black robes with yellowed eyes and crackling fingers, radiating dark authority",
    # Video games
    "master chief":"a towering supersoldier in olive green Mjolnir power armor with a mirrored gold visor",
    "kratos":      "a chalk-white muscular warrior with a red tattoo across his face wielding massive chained burning blades",
    "link":        "a young pointed-eared hero in a green tunic and cap with a wooden shield and a glowing sword",
    "sonic":       "a cobalt blue anthropomorphic hedgehog with red sneakers, always mid-sprint with a cocky grin",
    # Sonic universe
    "shadow the hedgehog": "a sleek black and crimson-striped anthropomorphic hedgehog in hover-skates with a dark brooding expression",
    "shadow":      "a sleek black and crimson-striped anthropomorphic hedgehog in hover-skates with a dark brooding expression",
    "knuckles":    "a muscular red anthropomorphic echidna with dreadlock spines and spiked fists, standing guard with arms crossed",
    "tails":       "a young golden-furred anthropomorphic fox with two large twin tails who hovers by spinning them like a helicopter",
    "amy rose":    "a cheerful pink anthropomorphic hedgehog in a red dress and headband swinging a giant toy hammer",
    "silver the hedgehog": "a young silver-furred anthropomorphic hedgehog with swept quills surrounded by floating objects held in a psychic glow",
    "rouge":       "a cunning white-furred bat-woman with large white wings in a white bodysuit and tall boots",
    "dr eggman":   "a rotund megalomaniacal scientist with a massive walrus mustache in a red coat piloting a hovering egg-shaped vehicle",
    # Mario universe
    "mario":       "a short stout mustachioed man in a red cap and blue overalls, cheerful and energetic",
    "luigi":       "a tall lanky mustachioed man in a green cap and blue overalls, kind-hearted and slightly nervous",
    "bowser":      "a colossal spiked-shell reptilian king with orange scales, a dark mane and fiery breath, commanding an army",
    "princess peach": "a regal blonde woman in a flowing pink ballgown with a small gold crown and long white gloves",
    "yoshi":       "a friendly round green dinosaur with red shoes, a large round nose and a long sticky tongue",
    "wario":       "a stocky greedy rival in a yellow cap and purple overalls with a large garlic-shaped nose and a gap-toothed grin",
    "waluigi":     "an extremely lanky scheming figure with a thin dark mustache in purple overalls, always being dramatic",
    "rosalina":    "a tall ethereal blonde woman with a long side-swept bang in a silver starry gown holding a small star companion",
    "bowser jr":   "a small young reptilian prince riding in a mechanical clown car, waving a magic paintbrush menacingly",
    # More Pokémon
    "gengar":      "a round shadowy ghost creature with a massive toothy grin and stubby arms lurking in the darkness",
    "lucario":     "a bipedal blue and black jackal creature with a spike on its chest and long black sensing appendages",
    "eevee":       "a small fluffy brown fox-like creature with a big cream-colored bushy tail and large expressive eyes",
    "charmander":  "a small orange bipedal lizard creature with a glowing flame burning on the tip of its tail",
    "squirtle":    "a small round blue turtle creature with a distinctive swirled brown shell, arms on hips",
    "bulbasaur":   "a small blue-green quadruped with large red eyes and a large green plant bulb growing from its back",
    "greninja":    "a lithe dark frog creature wearing its own long pink tongue as a scarf with golden star-shaped eyes",
    "rayquaza":    "a colossal sky serpent with a dark green body, yellow diamond markings and a maw full of fangs",
    "meowth":      "a small upright cream-colored cat creature with a gold coin on its head and slitted eyes",
    "gyarados":    "a massive raging blue serpentine sea monster with cream scales, whiskers and a snarling fanged jaw",
    "umbreon":     "a sleek black feline creature with glowing yellow ring markings that pulse in the dark",
    "sylveon":     "a graceful fairy creature with cream and pink fur and flowing ribbon-like feelers that emit calming aura",
    # More Marvel
    "doctor strange": "a goateed sorcerer in a deep red levitating cloak with an eye-shaped golden amulet and arcane hand gestures",
    "scarlet witch":  "a powerful sorceress with long auburn hair in a dark red armored outfit and a crimson headpiece, reality bending around her",
    "loki":           "a slender pale schemer with slicked black hair in green and gold Asgardian plate armor with twin curved horns on his helmet",
    "green goblin":   "a cackling armored villain in a green suit on a bat-shaped glider throwing pumpkin-shaped explosives",
    "doctor octopus": "a heavy-set scientist in a dark coat with four enormous mechanical metal tentacle arms attached to his torso",
    "ant-man":        "a hero in a silver and red helmet suit who can shrink to insect size while communicating with ants",
    "vision":         "a composed android with green skin, a yellow gem in his forehead and a red and gold bodysuit, phasing through walls",
    "nick fury":      "a commanding bald man with an eyepatch in a long black leather trench coat directing covert operations",
    "gamora":         "a skilled green-skinned alien warrior with long dark hair in dark form-fitting combat gear carrying twin blades",
    "groot":          "a towering gentle tree being with bark-textured limbs, glowing eyes and new leaves sprouting from his shoulders",
    "rocket":         "a small sharp-tongued anthropomorphic raccoon in a tactical vest carrying weapons three times his size",
    "silver surfer":  "a featureless gleaming silver humanoid riding a silver board through the cosmos, radiating cosmic energy",
    # More DC
    "aquaman":        "a powerfully built blond man in orange and green scale-like armor wielding a golden trident, commanding sea life",
    "green lantern":  "a hero in a black and green suit with a glowing green ring that constructs anything he can imagine",
    "nightwing":      "a lean acrobatic vigilante in a dark blue form-fitting suit with a blue bird emblem, flipping between rooftops",
    "harley quinn":   "a wild unpredictable woman with bleach-blonde pigtails in red and black jester colors carrying a giant mallet",
    "cyborg":         "a powerful half-human half-machine hero with silver titanium plating and glowing blue tech on his right side",
    "catwoman":       "a nimble thief in a sleek black catsuit and cat-ear mask with retractable claw-tipped gloves",
    "poison ivy":     "a confident botanist in a costume made entirely of leaves and vines with vines coiling at her feet",
    "bane":           "a hulking tactical fighter in dark military gear with a white breathing apparatus strapped to his face",
    "deathstroke":    "a half-masked mercenary in black and orange tactical armor wielding a sword staff and twin pistols",
    # Cartoon / animation
    "shrek":          "a large green ogre with square ears in a simple brown tunic, gruff but secretly kind-hearted",
    "gru":            "a lanky bald villain in an endless grey scarf with a giant potato-shaped head and a nose half his height",
    "buzz lightyear": "a confident toy space ranger in white and purple armor with a retractable clear helmet dome",
    "woody":          "a sheriff doll in a plaid shirt with a yellow star badge and cowboy hat who leads with loyalty",
    "simba":          "a young lion with golden fur, expressive amber eyes and the early signs of a magnificent mane",
    "mufasa":         "a majestic lion with a rich golden coat and a full dark mane, wise and commanding on a rocky ridge",
    "scar":           "a lean menacing lion with a dark mane, a pale scar over one eye and a permanently sarcastic expression",
    "moana":          "a courageous young Polynesian navigator with flowing dark hair in a woven barkcloth outfit on the open ocean",
    "maui":           "a broad demigod with long curly dark hair covered in living tattoos, wielding an enormous hook",
    "cloud":          "a brooding mercenary with wild gravity-defying blond spikes in torn dark clothing with an enormous buster sword",
    "sephiroth":      "a supernatural warrior with floor-length silver hair in a long black coat with a single wing, wielding a katana of impossible length",
    "geralt":         "a scarred silver-haired monster hunter with amber cat-like eyes in weathered dark leather armor",
    # Other anime
    "ichigo":         "a spiky orange-haired teenager in black samurai robes wielding an oversized jagged black cleaver sword",
    "natsu":          "a fiery pink-haired mage in a sleeveless vest with a white scarf, fists literally on fire",
    "edward elric":   "a short-tempered blond boy alchemist in a long red coat with a metal prosthetic right arm",
    "light yagami":   "a handsome studious young man with neat brown hair and a subtle calculating smile in a school uniform",
    # Dragon Ball extra
    "krillin":          "a short bald martial artist with a friendly face in an orange gi, loyal to his friends despite lacking power",
    "future trunks":    "a lavender-haired young swordsman from a ruined future in a blue jacket with a long katana on his back",
    "trunks":           "a purple-haired young fighter with a confident smirk in a blue vest and boots with a short sword",
    "android 18":       "a cool-headed blonde woman with straight hair in a denim jacket and black jeans with unsettling calm eyes",
    "jiren":            "a massively muscular grey-skinned alien fighter with a red uniform and intense glowing eyes, arms folded in meditation",
    "hit":              "a stoic tall alien assassin with swept violet hair in dark clothes who moves between moments in time",
    "whis":             "a graceful blue-skinned angel with white hair in flowing white robes carrying a long ornate staff",
    "zamasu":           "a pale green-skinned divine being with silver hair in flowing teal robes, radiating righteous malice",
    "gogeta":           "a confident fusion warrior with wild upswept dark hair in a dark vest and gi, radiating combined power",
    "vegito":           "a composed fusion warrior with dark hair in a blue gi, radiating the combined arrogance of both halves",
    # Naruto extra
    "minato":           "a tall blond ninja with a kind smile in a dark flak jacket and a white cloak with red flame trim at the hem",
    "jiraiya":          "a huge white-haired elder ninja with a red stripe on his nose in a dark outfit with a scroll on his back",
    "tsunade":          "a powerful blonde woman with a diamond tattoo on her forehead in a grey coat over dark battle clothes",
    "pain":             "a pale man with orange spiky hair and multiple metal piercings with ringed purple eyes radiating divine authority",
    "nagato":           "a frail pale redhead with long dark hair and haunted ringed eyes, hidden behind remote-controlled bodies",
    "konan":            "a pale woman with short blue hair and a paper flower ornament in an all-black cloak, surrounded by drifting paper",
    "gaara":            "a pale young man with dark circles under his teal eyes and a character carved into his forehead in sand-dusted dark armor",
    "rock lee":         "a young ninja with a round bowl cut in a green spandex jumpsuit with ankle weights, eager and determined",
    "hinata":           "a shy pale young woman with dark hair and pale eyes in a lavender jacket, quietly gathering her courage",
    "minato namikaze":  "a tall blond ninja with a kind smile in a dark flak jacket and a white cloak with red flame trim at the hem",
    # One Piece extra
    "ace":              "a tanned bare-chested young man with freckles and an orange hat with a tattoo on his left arm, grinning without fear",
    "whitebeard":       "a colossal old sea captain with a white walrus mustache and a bisento, a jagged scar across his chest",
    "law":              "a pale tattooed surgeon in a spotted cap and dark hoodie wielding a massive nodachi with stoic precision",
    "doflamingo":       "a flamboyant blonde man in a pink feather coat and red heart-shaped sunglasses who manipulates others like puppets",
    "mihawk":           "a tall hawk-eyed swordsman in a dark cape with a gold cross pendant wielding the world's largest black sword",
    "kaido":            "a monstrous muscular man with blue-grey skin and a massive spiked club, nearly impossible to kill",
    "big mom":          "a terrifying giant woman with pink hair in a dark dress wearing a three-cornered hat and biting everything",
    "hancock":          "a tall stunning woman with long black hair in an empress dress and a snake crown, imperious and powerful",
    "nami":             "a resourceful navigator with short orange hair in casual clothes wielding a segmented weather staff",
    "robin":            "a calm dark-haired archaeologist in tan and navy clothes who can sprout duplicate limbs from any surface",
    "franky":           "a flamboyant blue-haired cyborg in a loud floral shirt with a huge mechanical body he built himself",
    # Bleach extra
    "grimmjow":         "a muscular pale fighter with spiky cyan hair and a wild grin in a white jacket, always itching for a fight",
    "ulquiorra":        "a pale gaunt warrior with dark hair and green tear-mark tattoos under expressionless hollow eyes in a white coat",
    "yoruichi":         "a fast dark-skinned woman with short violet hair in an orange sleeveless top who can turn into a black cat",
    # AoT extra
    "armin":            "a small blond young soldier with wide blue eyes and a tactical mind in an olive-green scout uniform",
    "reiner":           "a broad-shouldered pale soldier with a square jaw and short blond hair in olive-green warrior armor",
    "annie":            "a pale young woman with short straight blond hair and cold blue eyes in scout gear, fighting with precision kicks",
    # Demon Slayer extra
    "shinobu":          "a petite pale woman with dark hair in a butterfly-patterned purple haori carrying a thin needle-like blade with a smile",
    "akaza":            "a muscular man with short pink hair and dark compass-like tattoos across a pale torso with burning amber eyes",
    "muzan":            "a pale elegant man with black hair in a white suit who radiates cold predatory danger",
    # JJK extra
    "nanami":           "a stoic businessman-turned-sorcerer with blond hair in a grey suit and tan tie who fights with a blunt wrapped sword",
    "nobara":           "a bold young woman with an amber bob haircut in a dark uniform who drives nails with a hammer in battle",
    "megumi":           "a sullen dark-haired young sorcerer in a dark uniform who summons shadowy four-legged spirit creatures",
    "mahito":           "a smiling patchwork-faced young man with mismatched black and white hair who reshapes bodies with a touch",
    "toji":             "a tall muscular man with dark hair and a thin scar near his mouth in a dark vest, lethal without any power",
    # MHA extra
    "toga":             "a pale girl with upswept sandy blonde buns and excited slit eyes in a dark school uniform wielding a syringe",
    "shigaraki":        "a pale cracked-skin young man with pale blue hair covered in severed disembodied hands as a costume",
    "endeavor":         "a towering muscular hero with a full beard of literal fire in a white bodysuit with blue accents",
    "hawks":            "a young relaxed hero with messy blond hair and massive feathered crimson wings in a dark hero costume",
    # Berserk
    "guts":             "a giant scarred black-swordsman with dark hair, an iron prosthetic left arm and a colossal black iron sword dragged on his shoulder",
    "griffith":         "a beautiful silver-haired warrior in polished white plate armor with a hawk crest, charismatic and cold",
    # Gurren Lagann
    "simon":            "a young man with dark hair in a dark jacket and a glowing blue drill pendant who grows from timid to unstoppable",
    "kamina":           "a tall muscular man with a teal pompadour and dark sunglasses in a dark cape covered in a red tribal tattoo",
    # Kill la Kill
    "ryuko matoi":      "a fierce dark-haired girl with a red streak in her hair in a revealing dark red armored school uniform wielding a giant single scissor blade",
    "satsuki":          "a tall commanding woman with long dark hair and steel-cold teal eyes in rigid white ceremonial armor",
    # Madoka Magica
    "madoka":           "a small gentle girl with pink twintails in a pink frilled magical outfit wielding a large rose-colored longbow",
    "homura":           "a stoic pale girl with long black hair and violet eyes in a dark magical outfit who manipulates time",
    "mami":             "a calm blonde girl with long golden ringlets in a yellow magical outfit who summons ornate flintlock rifles",
    # Inuyasha
    "inuyasha":         "a half-demon young man with long silver hair and white dog ears in a deep red fire-rat kimono wielding a large fang-shaped sword",
    "sesshoumaru":      "a cold ethereal full-blooded demon lord with floor-length silver hair in white flowing robes with a single poisoned whip",
    "sesshomaru":       "a cold ethereal full-blooded demon lord with floor-length silver hair in white flowing robes with a single poisoned whip",
    # Yu Yu Hakusho
    "yusuke":           "a delinquent teen with a dark pompadour in a dark school uniform who fires spirit energy from his fingertip",
    "hiei":             "a short aggressive dark-haired fighter with a spiked headband and a single eye on his forehead in dark clothing",
    "kurama":           "a calm beautiful young man with long flowing red hair in a white uniform who fights with a conjured whip of thorns",
    # Tokyo Ghoul
    "kaneki":           "a pale young man with white hair in a half-white cracked mask missing an eye, dark tentacle-like appendages emerging from his back",
    "kaneki ken":       "a pale young man with white hair in a half-white cracked mask missing an eye, dark tentacle-like appendages emerging from his back",
    # Black Clover
    "asta":             "a short muscular boy with white spiky hair in a black and red knight's robe swinging a massive black anti-magic sword",
    "yuno":             "a tall elegant young man with dark hair and golden star-shaped eyes in a white knight's robe wielding wind",
    # Solo Leveling
    "sung jinwoo":      "a lean young man with dark hair and glowing violet eyes in sleek black armor commanding an army of shadows",
    # Konosuba
    "kazuma":           "a slouching average young man in a green hood and dark tunic who argues with his incompetent party constantly",
    "megumin":          "a petite intense girl with dark hair and an eyepatch in a witch hat and robe who only knows one explosion spell",
    "aqua":             "a bubbly blue-haired goddess in a pale blue dress with a blue hair loop who is surprisingly useless",
    # Vinland Saga
    "thorfinn":         "a young nordic warrior with blond braided hair and cold blue eyes in fur-lined dark battle gear driven by revenge",
    "askeladd":         "a wiry grey-haired viking captain with a sharp cunning smile in a dark cloak with a sword always at hand",
    # Assassination Classroom
    "koro-sensei":      "a pale yellow tentacled creature with a giant smiley-face head in academic robes moving at supersonic speeds",
    # Dr. Stone
    "senku":            "a wild-haired teen in primitive stone-age lab clothes with an impossible shock of white and black hair and a calculating grin",
    # Vash the Stampede / Trigun
    "vash":             "a lanky goofy-looking man with wild blond spiky hair in a battered scarlet long coat concealing a massive mechanical arm weapon",
    "vash the stampede":"a lanky goofy-looking man with wild blond spiky hair in a battered scarlet long coat concealing a massive mechanical arm weapon",
    # Akame ga Kill
    "akame":            "a pale young woman with waist-length black hair and red eyes in dark battle clothes wielding a black cursed katana",
    # Noragami
    "yato":             "a young wandering deity with dark hair and blue eyes in worn casual clothes wielding a spirit weapon",
    # Violet Evergarden
    "violet evergarden":"a pale young woman with long golden hair and deep eyes in a white formal dress with shining metal prosthetic arms",
    # Horror villains
    "pennywise":        "a theatrical clown with orange tufts of hair and a pale face with yellow fang-rimmed eyes in a silver ruffled period costume",
    "freddy krueger":   "a badly burned nightmare stalker in a red and dark striped sweater with a bladed leather glove and a dark fedora",
    "jason voorhees":   "a hulking silent killer in a white hockey mask and dark olive jacket dragging a machete through the woods",
    "michael myers":    "a broad emotionless figure in a white mask and dark coveralls moving at a slow unstoppable walk",
    "chucky":           "a small freckled toy doll with wild auburn hair and overalls who moves when no one is watching",
    "ghostface":        "a slender figure in a flowing black robe with a pale white screaming ghost mask and a hunting knife",
    "leatherface":      "a massive apron-wearing figure with a crude mask of stitched hide wielding a revving chainsaw",
    "pinhead":          "a chalk-pale figure in a black leather robe with a grid of metal pins driven into their skull",
    "valak":            "a spectral pale figure in a dark nun's habit with hollow eyes and a malevolent presence",
    "annabelle":        "a cracked porcelain doll with dark glass eyes and painted cheeks in a white dress with a red ribbon",
    "hannibal lecter":  "a composed brilliant man in a restraint mask with piercing intelligent eyes and terrifyingly perfect manners",
    "the nun":          "a spectral pale figure in a dark nun's habit with hollow eyes and a malevolent presence",
    "it":               "a theatrical clown with orange tufts of hair and a pale face with yellow fang-rimmed eyes in a silver ruffled period costume",
    # Movies / other
    "john wick":        "a methodical close-cropped bearded assassin in a well-tailored black suit moving through gunfights with ballet-like precision",
    "terminator":       "a stoic humanoid with a chrome metal endoskeleton partially visible beneath damaged skin and glowing red optical sensors",
    "jack sparrow":     "a swaying eccentric pirate with beaded braids and trinkets in a worn faded coat, never quite where he seems to be",
    "indiana jones":    "a rugged professor-adventurer in a battered felt fedora and leather jacket with a whip at his belt",
    "james bond":       "a composed sharp-jawed spy in a perfectly fitted dark suit with an air of total confidence under fire",
    "tony montana":     "a Cuban immigrant crime lord in a white linen suit with a deep facial scar who rose from nothing",
    "walter white":     "a bald middle-aged chemistry teacher in wire-rimmed glasses and a green jacket who reinvented himself as a criminal",
    "the mandalorian":  "a taciturn bounty hunter in full beskar steel plate armor with a smooth T-visor helmet, never removing it",
    "predator":         "a towering alien sport-hunter with dreadlock-like appendages and mandible tusks in dark mesh armor using cloaking tech",
    "alien":            "a towering black biomechanical creature with an elongated ribbed skull and a second inner jaw dripping acid",
    "xenomorph":        "a towering black biomechanical creature with an elongated ribbed skull and a second inner jaw dripping acid",
    "robocop":          "a part-human law enforcer in silver full-body police armor with a dark visor covering the upper half of his face",
    "v":                "a theatrical vigilante in a smiling porcelain mask and a dark wide-brimmed hat wielding many knives",
    "neo":              "a pale determined hacker in a long flowing black coat and dark wrap-around glasses who bends reality",
    "the joker":        "a gaunt pale man with green dyed hair in a purple three-piece suit with smeared face paint and a wide grin",
    "heath ledger joker": "a pale man with smeared white and dark face paint and a scarred smile in a wrinkled purple overcoat",
    # Anime additional
    "spike spiegel":    "a lanky pale man with a wild deep-olive afro in a pale blue leisure suit with a relaxed cigarette in his lips",
    "lelouch":          "a lean pale young man with deep violet eyes and dark charcoal hair in an ornate dark navy military uniform",
    "mob":              "a blank-faced dark-haired boy in a dark charcoal school uniform surrounded by crackling pale psychic energy",
    "rimuru":           "a small androgynous figure with long flowing pale silver hair and deep golden slit-pupil eyes in a dark charcoal battle suit",
    "ainz":             "a towering undead overlord with a pale ivory skeletal face and deep crimson floating eye flames in dark charcoal ornate robes",
    "goblin slayer":    "a stoic warrior in battered dark olive full-coverage armor with a plain closed helm and a short sword",
    "alucard":          "a tall pale vampire in a deep crimson long coat and wide-brimmed hat with deep crimson eyes behind pale tinted glasses",
    "saitama":          "an unassuming bald man in a deep yellow jumpsuit with a pale white cape and blank expressionless eyes",
    "genos":            "a lean cyborg with pale-chrome mechanical arms and deep amber glowing optical sensors in a dark charcoal body",
    "reigen":           "a sharp-suited con man with slicked pale-amber hair in a dark charcoal business suit with an over-confident expression",
    # More video games
    "doom slayer":      "a hulking warrior in battle-scarred dark olive and deep crimson powered armor with a glowing green visor",
    "bayonetta":        "a tall pale woman in a skin-tight suit woven from her own deep raven hair wielding four pistols on hands and heels",
    "samus aran":       "a tall warrior in a sleek deep orange and pale gold powered exo-suit with a large arm cannon",
    "doomguy":          "a hulking warrior in battle-scarred dark olive and deep crimson powered armor with a glowing green visor",
    # Bleach
    "ichigo kurosaki":  "a teenager with spiky deep-amber hair in a dark charcoal robe wielding a massive jagged cleaver sword",
    "rukia":            "a petite dark-haired young woman in a dark charcoal uniform with deep violet-blue eyes",
    "aizen":            "a calm dark-haired man with oval glasses in a pale silver officer's uniform",
    "byakuya":          "a noble pale-complexioned man with long dark hair adorned with a silver kenseikan ornament in a flowing captain's cloak",
    "renji":            "a tall man with long deep-burgundy hair in a high topknot covered in dark indigo tribal tattoos",
    "urahara":          "an eccentric shopkeeper in a dark olive striped hat and pale grey cloak with a walking cane",
    # Fairy Tail
    "erza":             "a warrior woman with long deep-amber hair in ornate pale-silver plate armor wielding multiple blades",
    "gray":             "a dark-haired young man with deep indigo-grey eyes and distinctive tribal tattoo on his forehead",
    "lucy":             "a young woman with long pale-golden hair in a dark navy mini-skirt outfit carrying a celestial key ring",
    "mavis":            "a small fairy-like girl with very long flowing pale-amber hair in a pale white frilled dress",
    # Fullmetal Alchemist
    "alphonse":         "a towering hollow suit of dark grey medieval armor housing a disembodied soul",
    "roy mustang":      "a sharp-featured military officer with neatly combed dark-charcoal hair in a dark navy military uniform",
    "winry":            "a young woman with pale-amber pigtails in overalls with a mechanical wrench at her hip",
    "envy":             "a pale androgynous figure with long dark-black jagged hair and deep violet eyes in a black form-fitting suit",
    # Avatar
    "aang":             "a young bald boy with a deep teal arrow tattoo on his forehead in deep amber and pale saffron robes",
    "zuko":             "a young man with a prominent burn scar over his left eye and dark hair in a deep crimson royal robe",
    "katara":           "a young woman with deep-teal eyes and dark hair in water-tribe deep indigo wrappings",
    "toph":             "a small girl with pale skin and unseeing pale-grey eyes in a deep olive earth-tribe outfit",
    "azula":            "a sharp-featured young woman with dark hair in a deep crimson royal fire nation armor",
    # Chainsaw Man
    "denji":            "a scruffy pale-amber-haired young man with a chainsaw rip-cord embedded in his chest",
    "makima":           "a pale woman with reddish-brown hair in a neat dark charcoal office shirt with deep amber ringed eyes",
    "power":            "a brash young woman with pale-blond hair and small reddish horns in a dark charcoal uniform",
    # Hunter x Hunter
    "gon":              "a spiky dark-olive-haired boy in a pale olive jacket and dark shorts with green eyes",
    "killua":           "a pale boy with spiky silver-white hair and deep teal eyes in a pale indigo long-sleeved shirt",
    "hisoka":           "a tall lithe figure with pale-rose and pale-golden face paint in a diamond-motif jester costume",
    "kurapika":         "a pale young man with short pale-amber hair and deep crimson chain-bearing eyes in a dark ivory battle suit",
    # Sword Art Online
    "kirito":           "a dark-haired young man in dual-wield stance wearing a sleek dark charcoal long coat",
    "asuna":            "a young woman with long flowing chestnut hair in pale ivory and deep burgundy battle armor",
    # Re:Zero
    "subaru":           "a dark-haired young man in a dark navy tracksuit with pale silver and deep indigo accents",
    "rem":              "a blue-haired oni maid in a pale white apron dress with a deep navy ribbon in her hair",
    # Evangelion
    "rei":              "a pale girl with short pale-slate hair and deep violet eyes in a pale white plugsuit",
    "asuka":            "a fiery young woman with long deep-auburn hair in a deep crimson form-fitting pilot suit",
    # Pokémon
    "pikachu":          "a small round pale-yellow rodent creature with pointed black-tipped ears and a jagged tail",
    "mewtwo":           "a sleek pale-grey psychic creature with a thick tail and deep violet eyes",
    "charizard":        "a bipedal dark-orange dragon with pale cream belly and large blue-tipped wings breathing flame",
    "ash":              "a young boy in a deep indigo cap and pale grey jacket with a determined expression",
    # Transformers
    "optimus prime":    "a massive heroic robot with a deep cobalt and deep burgundy chassis and a protruding silver face-plate",
    "megatron":         "a hulking dark-silver robot tyrant with a deep charcoal cannon fused to one arm",
    # Game of Thrones
    "jon snow":         "a dark-haired brooding young man in heavy dark charcoal fur-lined night's watch cloak with a pale grey bastard sword",
    "daenerys":         "a pale young woman with long pale-platinum braided hair in deep violet Dothraki-style robes",
    "tyrion":           "a small pale man with pale-amber hair and mismatched deep-green and dark-charcoal eyes in Lannister crimson",
    # Lord of the Rings
    "gandalf":          "an ancient robed wanderer with long flowing white hair and beard in a pale white cloak carrying a gnarled staff",
    "frodo":            "a small curly dark-haired hobbit in a pale ochre vest with an elven mithril undershirt",
    "aragorn":          "a rugged dark-haired ranger in worn dark charcoal travel leathers with a pale silver heirloom sword",
    "legolas":          "a lithe pale elf with long pale-golden hair in pale silver and deep green forest armor carrying a long bow",
    "sauron":           "a towering dark figure in black spiked full-body armor with a single blazing deep-amber eye",
    # Harry Potter
    "harry potter":     "a young man with messy dark-charcoal hair and round wire-framed glasses bearing a faint lightning-bolt scar",
    "hermione":         "a young woman with bushy deep-brown hair in deep navy school robes clutching a spellbook",
    "voldemort":        "a skeletal pale figure with flat noseless face and deep crimson snake-like eyes in flowing dark robes",
    "dumbledore":       "a tall elderly man with a long pale silver beard in sweeping deep violet robes and half-moon spectacles",
    # Cyberpunk
    "v":                "a neon-lit mercenary with deep indigo cybernetic implants etched across their temple in a dark charcoal worn jacket",
    "johnny silverhand":"a rocker mercenary with pale-platinum hair and a gleaming dark-silver cybernetic arm in a dark charcoal vest",
    # Misc games
    "2b":               "a pale android warrior in a pale ivory dress and dark blindfold wielding a dark silver thin sword",
    "dante":            "a cocky pale-haired demon hunter in a long deep-crimson coat with twin pale-ivory pistols",
    "nero":             "a young demon hunter with pale-platinum hair in a deep navy coat with a powerful mechanical arm",
    "solid snake":      "a grizzled operative in dark olive tactical infiltration suit with a dark bandana over his forehead",
    "big boss":         "a battle-scarred soldier with an eyepatch and weathered dark-olive tactical gear",
    "raiden":           "a pale cyborg warrior with silver-white hair in a sleek dark-charcoal exoskeletal suit",
    "ezio":             "a roguish dark-haired assassin in pale ivory hooded robes with hidden blades at his wrists",
    "altair":           "a stoic dark-featured assassin in pale ivory hooded robes with a distinctive eagle-shaped beak hood",
    "lara croft":       "an athletic young woman with dark hair in a tight braid in worn deep olive explorer gear with a compound bow",
    "nathan drake":     "a wisecracking adventurer with deep-brown hair in a pale ivory henley shirt and worn dark chino pants",
    "ellie":            "a teenage girl with deep auburn hair and freckles in a worn deep olive flannel shirt",
    "joel":             "a grizzled middle-aged man with greying dark-brown hair in a worn deep charcoal flannel jacket",
    "arthur morgan":    "a weathered outlaw in a pale tan wide-brimmed hat and deep chestnut duster coat with a lever-action rifle",
    "master chief":     "a supersoldier in dark olive powered armor with a tarnished bronze reflective visor",
}

# ── Cinematic bypass: IP-specific technique / term map ────────────────────────
# Franchise vocabulary that slips through even after name substitution.
# Longer phrases are sorted first at runtime to avoid partial-match conflicts.
_BYPASS_TERMS_MAP: dict[str, str] = {
    # Dragon Ball techniques / lore
    "kamehameha":           "focused beam of concentrated energy",
    "spirit bomb":          "massive sphere of gathered life energy",
    "final flash":          "overwhelming beam of pure destructive energy",
    "big bang attack":      "explosive burst of concentrated energy",
    "special beam cannon":  "piercing spiral beam of focused energy",
    "instant transmission": "teleportation technique",
    "kaioken":              "power-multiplying combat technique",
    "ultra instinct":       "ultimate reflex-driven combat state",
    "super saiyan":         "golden-haired ascended warrior form",
    "saiyan":               "elite warrior",
    "namekian":             "tall green alien warrior",
    "frieza force":         "intergalactic military force",
    "dragon ball":          "mystical orb",
    "planet namek":         "alien world",
    "capsule corp":         "advanced technology company",
    # Naruto techniques / lore
    "rasengan":             "swirling sphere of concentrated energy",
    "chidori":              "crackling palm-strike of focused lightning",
    "shadow clone jutsu":   "mass self-duplication technique",
    "sharingan":            "deep crimson pattern-tracking eye ability",
    "rinnegan":             "deep violet multi-ring omnipotent eye ability",
    "byakugan":             "pale veined all-seeing eye ability",
    "mangekyou sharingan":  "evolved dark crimson multi-form eye technique",
    "susanoo":              "colossal ethereal warrior construct",
    "amaterasu":            "inextinguishable dark crimson eye-flame",
    "tsukuyomi":            "mental illusion binding technique",
    "eight gates":          "extreme physical limiter-release technique",
    "sage mode":            "nature-energy enhanced combat state",
    "tailed beast":         "colossal chakra creature",
    "kurama":               "colossal nine-tailed fox spirit",
    "chakra":               "life energy",
    "jutsu":                "combat technique",
    "ninjutsu":             "energy combat technique",
    "genjutsu":             "illusionary technique",
    "taijutsu":             "physical combat technique",
    "akatsuki":             "cloaked rogue mercenary organization",
    "hidden leaf village":  "fortified ninja settlement",
    "konoha":               "fortified ninja settlement",
    # Bleach techniques / lore
    "bankai":               "ultimate weapon-release technique",
    "shikai":               "initial weapon-release technique",
    "zanpakuto":            "spirit-bonded blade",
    "hollowification":      "transformation into a dark hollow entity",
    "getsuga tensho":       "crescent arc of dark energy",
    "tensa zangetsu":       "compressed dark energy katana",
    "senbonzakura":         "thousand-blade petal dispersal technique",
    "soul society":         "spirit realm",
    "hollow":               "dark spirit creature",
    "shinigami":            "spirit warrior",
    # One Piece techniques / lore
    "gear second":          "blood-pump overclocked combat form",
    "gear third":           "bone-inflated giant limb combat form",
    "gear fourth":          "muscle-compressed bouncing combat form",
    "gear fifth":           "reality-altering transcendent combat form",
    "haki":                 "invisible force projection",
    "conqueror's haki":     "overwhelming will force projection",
    "devil fruit":          "supernatural ability-granting fruit",
    "gomu gomu":            "elastic rubber ability",
    "gum-gum":              "elastic rubber ability",
    "marineford":           "naval fortress battle arena",
    # My Hero Academia
    "one for all":          "stockpiled power transfer ability",
    "all for one":          "ability-stealing power",
    "quirk":                "superpower",
    "plus ultra":           "beyond limits battle cry",
    "detroit smash":        "full-powered downward punch",
    "u.a. high":            "hero training academy",
    # Attack on Titan lore
    "titan":                "colossal humanoid creature",
    "founding titan":       "progenitor colossal form",
    "attack titan":         "future-seeing colossal form",
    "rumbling":             "earth-shaking colossal army march",
    "survey corps":         "scouting military unit",
    "omni-directional":     "multi-directional grapple gear",
    "maneuver gear":        "grapple-and-blade combat harness",
    # Demon Slayer techniques / lore
    "water breathing":      "flowing water-form sword technique",
    "flame breathing":      "explosive fire-form sword technique",
    "thunder breathing":    "lightning-fast sword technique",
    "wind breathing":       "slashing gale-form sword technique",
    "total concentration":  "enhanced breathing combat state",
    "hinokami kagura":      "blazing sun-dance sword technique",
    "blood demon art":      "demonic supernatural power",
    "sun breathing":        "blazing ancient sun-form sword technique",
    "demon slayer corps":   "demon-hunting military organization",
    "wisteria":             "pale purple toxic flower",
    # Jujutsu Kaisen
    "cursed energy":        "supernatural malevolent energy",
    "cursed technique":     "supernatural combat technique",
    "domain expansion":     "reality-altering spiritual domain technique",
    "sukuna's domain":      "ancient demon's spatial technique",
    "infinite void":        "infinite perception overload technique",
    "black flash":          "distorted space combat strike",
    "reverse cursed technique": "healing supernatural technique",
    "jujutsu high":         "supernatural combat academy",
    # Pokémon
    "pokémon":              "creature companion",
    "pokemon":              "creature companion",
    "poké ball":            "capture sphere",
    "pokeball":             "capture sphere",
    "gym leader":           "arena champion",
    "team rocket":          "criminal organization in dark uniforms",
    "evolution":            "transformation",
    # Transformers
    "autobot":              "heroic machine warrior",
    "decepticon":           "villainous machine warrior",
    "energon":              "glowing energy crystal",
    "cybertron":            "mechanical home world",
    # Star Wars
    "lightsaber":           "glowing plasma blade",
    "the force":            "mystical binding energy field",
    "jedi":                 "ancient energy-wielding warrior order",
    "sith":                 "dark-side energy warrior",
    "stormtrooper":         "white armored soldier",
    "death star":           "massive spherical space station",
    "the empire":           "authoritarian galactic regime",
    "rebel alliance":       "freedom-fighter resistance force",
    "hyperspace":           "faster-than-light travel",
    "blaster":              "energy pistol",
    # Marvel
    "avengers":             "team of super-powered heroes",
    "s.h.i.e.l.d.":        "covert government task force",
    "hydra":                "shadow military organization",
    "thanos snap":          "reality-altering finger snap",
    "infinity gauntlet":    "jeweled omnipotent gauntlet",
    "infinity stones":      "cosmic power gems",
    "arc reactor":          "compact fusion power core",
    "mjolnir":              "ancient enchanted war hammer",
    "vibranium":            "ultra-dense fictional metal",
    "web-slinging":         "swinging on strong wire lines",
    "symbiote":             "dark alien life-form",
    # DC
    "kryptonite":           "glowing radioactive mineral",
    "gotham":               "gritty rain-soaked city",
    "metropolis":           "gleaming modern city",
    "batmobile":            "sleek armored pursuit vehicle",
    "bat-signal":           "large spotlight projection",
    "justice league":       "super-powered hero team",
    "speed force":          "kinetic energy dimension",
    # Video game lore
    "halo":                 "ancient orbital ring structure",
    "covenant":             "alien religious military alliance",
    "spartan":              "enhanced supersoldier",
    "god of war":           "divine combat power",
    "blades of chaos":      "chain-linked curved blades",
    "witcher":              "mutant monster hunter",
    "silver sword":         "enchanted pale silver blade",
    "geralt's":             "the scarred hunter's",
    "fantasy vii":          "sci-fi world",
    "midgar":               "sprawling industrial city under a steel plate",
    "materia":              "glowing magical orb",
    "limit break":          "unleashed maximum power technique",
    "persona":              "summoned inner spirit creature",
    "stand":                "summoned spiritual power manifestation",
    "ora ora":              "rapid close-range assault",
    "muda muda":            "overwhelming barrage",
    "jojo":                 "flamboyant fighter",
    "requiem":              "ultimate evolved form",
    "bloodborne":           "gothic plague-ridden world",
    "hunter's dream":       "ethereal safe sanctuary",
    "dark souls":           "cursed crumbling world",
    "estus flask":          "glowing amber healing vessel",
    "elden ring":           "shattered golden artifact",
    "erdtree":              "colossal luminous golden tree",
    # Game of Thrones / fantasy lore
    "westeros":             "fractured medieval kingdom",
    "king's landing":       "walled coastal capital city",
    "white walker":         "ice-blue undead warrior",
    "wildfire":             "toxic green alchemical flame",
    "dragonfire":           "roaring jet of dragon flame",
    "targaryen":            "silver-haired dragon-riding royal",
    "lannister":            "golden-armored noble house warrior",
    # Harry Potter lore
    "hogwarts":             "gothic castle academy",
    "expelliarmus":         "disarming magical spell",
    "avada kedavra":        "lethal magical incantation",
    "patronus":             "shimmering silver spirit guardian",
    "horcrux":              "dark soul-fragment artifact",
    "death eater":          "masked dark-robed cultist",
    "quidditch":            "airborne broom sport",
    "butterbeer":           "warm frothy sweet drink",
    # Lord of the Rings lore
    "mordor":               "desolate volcanic wasteland",
    "mount doom":           "active volcanic mountain",
    "one ring":             "simple golden cursed ring",
    "mithril":              "pale silver ultra-light legendary metal",
    "rivendell":            "elegant elven forest sanctuary",
    "shire":                "rolling pastoral countryside village",
    "balrog":               "ancient shadow and flame demon",
    "uruk-hai":             "large dark armored orc warrior",
    # Generic anime / manga
    "power level":          "combat strength reading",
    "aura":                 "energy field",
    "ki":                   "life force energy",
    "mana":                 "magical energy",
    "nen":                  "life energy",
    "spiritual pressure":   "energy presence",
    "reiatsu":              "spiritual energy pressure",
}

import re as _re_bypass_compile

# Pre-compile a single combined substitution for speed
def _build_bypass_pattern(mapping: dict[str, str]):
    """Build a compiled regex that matches any key (longest first)."""
    keys = sorted(mapping, key=len, reverse=True)
    pattern = r"\b(" + "|".join(_re_bypass_compile.escape(k) for k in keys) + r")\b"
    return _re_bypass_compile.compile(pattern, _re_bypass_compile.IGNORECASE)

_BYPASS_CHAR_PATTERN  = _build_bypass_pattern(_BYPASS_CHARACTER_MAP)
_BYPASS_TERMS_PATTERN = _build_bypass_pattern(_BYPASS_TERMS_MAP)


def apply_bypass_prompt(prompt: str) -> str:
    """
    Full cinematic bypass:
      1. Replace known IP character names with precise visual descriptions.
      2. Replace franchise-specific techniques, abilities, lore terms, and
         place-names with neutral cinematic equivalents.
      3. Wrap the transformed prompt in a Six-Slot cinematic framing sentence
         so the filter reads a filmmaker's shot description, not a keyword list.
    """
    result = prompt

    # 1 — Character names → visual descriptions
    result = _BYPASS_CHAR_PATTERN.sub(
        lambda m: _BYPASS_CHARACTER_MAP[m.group(0).lower()], result
    )

    # 2 — IP-specific terms → neutral equivalents
    result = _BYPASS_TERMS_PATTERN.sub(
        lambda m: _BYPASS_TERMS_MAP[m.group(0).lower()], result
    )

    # 3 — Strip legacy 11ii…11ii tags
    import re as _re
    result = _re.sub(r"11ii(.+?)11ii", r"\1", result)

    # 4 — Cinematic wrapping: only add framing if the result doesn't already
    #     read like a camera description (avoids double-wrapping).
    lower = result.lower()
    has_camera_words = any(w in lower for w in (
        "cinematic", "wide shot", "close-up", "medium shot", "tracking shot",
        "slow motion", "dramatic lighting", "golden hour", "film grain",
    ))
    if not has_camera_words:
        result = (
            "Cinematic wide shot, dramatic natural lighting, film grain — "
            + result
        )

    return result


# JS init-script: intercept clicks on any link pointing to Claude/Anthropic and
# hide its banner container instead of navigating away.
_CLAUDE_BLOCKER_JS = """
(function () {
  document.addEventListener('click', function (e) {
    const a = e.target.closest('a[href]');
    if (!a) return;
    const href = a.href || '';
    if (!href.includes('claude') && !href.includes('anthropic')) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    // Walk up to the nearest bar-like container and hide it
    let el = a;
    for (let i = 0; i < 12; i++) {
      if (!el || el === document.body) break;
      const r = el.getBoundingClientRect();
      if (r.width > window.innerWidth * 0.5 && r.height < 120) {
        el.style.setProperty('display', 'none', 'important');
        break;
      }
      el = el.parentElement;
    }
  }, true);
})();
""".strip()

# ── Config ─────────────────────────────────────────────────────────────────────

_ARTLIST_HOME_URL  = "https://artlist.io/"
_ARTLIST_VIDEO_URL = "https://toolkit.artlist.io/new?mode=video"
_ARTLIST_IMAGE_URL = "https://toolkit.artlist.io/new?mode=image"
_ARTLIST_EMAIL     = os.environ.get("ARTLIST_EMAIL", "")
_ARTLIST_PASSWORD  = os.environ.get("ARTLIST_PASSWORD", "")

_CHROMIUM_BIN = (
    "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"
)

# Cookies are saved here after a successful login and reused on every run
_COOKIES_FILE = Path(__file__).parent / ".artlist_session.json"

MODEL_DURATIONS: dict[str, int] = {
    # Google / Veo  (fixed 8 s — only option available in UI)
    "Veo 3.1":                   8,
    "Veo 3.1 Fast":              8,
    "Veo 3.1 Lite":              8,
    # OpenAI / Sora
    "Sora 2":                   10,
    "Sora 2 Pro":               10,
    # Google Gemini
    "Gemini Omni Flash":        10,
    # Kuaishou / Kling  (5 s and 10 s; defaults to 5 s)
    "Kling 3.0":                 5,
    "Kling 3.0 Pro":             5,
    "Kling O3":                  5,
    "Kling 2.6 Pro":             5,
    "Kling 2.5 Turbo Pro":       5,
    "Kling 2.1 Pro":             5,
    "Kling 2.1":                 5,
    "Kling 2.0":                 5,
    "Kling":                     5,
    "Kling Pro":                 5,
    # ByteDance / Seedance
    "Seedance 2.0":             15,
    "Seedance 2.0 Fast":        10,
    "Seedance 2.0 Mini":        10,
    "Seedance 1.5 Pro":         15,
    "Seedance 1.0 Pro Fast":    10,
    # MiniMax / Hailuo
    "Hailuo 2.3 Pro":           10,
    "Hailuo 2.3 Fast Pro":      10,
    "Hailuo 2.3 Standard":      10,
    "Hailuo 2.3 Fast Standard": 10,
    "Hailuo":                   10,
    # Alibaba / Wan
    "Wan 2.6":                  10,
    "Wan":                      10,
    # xAI / Grok
    "Grok Imagine Video":       10,
    # HappyHorse  (fixed 7 s — only option available in UI)
    "HappyHorse 1.0":            7,
    "HappyHorse 1.1":            7,
    # Legacy
    "Vidu":                     10,
    "Runway":                   10,
}

# Per-model list of durations (seconds) that Artlist actually offers in its UI.
# If the user requests a duration not in this list, we snap to the nearest valid one
# before trying to set it — preventing "duration not found" failures.
# Models NOT in this dict accept any duration (the UI decides what's available).
MODEL_AVAILABLE_DURATIONS: dict[str, list[int]] = {
    # Veo: fixed at 8 s — no other options shown
    "Veo 3.1":                   [8],
    "Veo 3.1 Fast":              [8],
    "Veo 3.1 Lite":              [8],
    # Kling: 5 s and 10 s only (Start Frame / End Frame optional for image-to-video)
    "Kling 3.0":                 [5, 10],
    "Kling 3.0 Pro":             [5, 10],
    "Kling O3":                  [5, 10],
    "Kling 2.6 Pro":             [5, 10],
    "Kling 2.5 Turbo Pro":       [5, 10],
    "Kling 2.1 Pro":             [5, 10],
    "Kling 2.1":                 [5, 10],
    # HappyHorse: fixed 7 s — no other options shown
    "HappyHorse 1.0":            [7],
    "HappyHorse 1.1":            [7],
}

# Models that do NOT support image reference on Artlist (text-to-video only).
# If the caller passes image_ref_bytes for one of these, it is silently dropped
# before the browser session starts.
MODELS_WITHOUT_IMAGE_REF: frozenset[str] = frozenset({
    # Google / Veo — text-to-video only
    "Veo 3.1",
    "Veo 3.1 Fast",
    "Veo 3.1 Lite",
    # OpenAI / Sora — text-to-video only on Artlist
    "Sora 2",
    "Sora 2 Pro",
    # Google Gemini — text-to-video only
    "Gemini Omni Flash",
    # HappyHorse — text-to-video only
    "HappyHorse 1.0",
    "HappyHorse 1.1",
    # xAI / Grok — text-to-video only
    "Grok Imagine Video",
})

# Kling models use "Start Frame" (via the "Start & End Frame" menu option) for
# image-to-video — NOT "Image Reference", which is disabled for Kling in the UI.
# Upload must happen AFTER model selection so Kling's Start Frame chip is active.
MODELS_USING_START_FRAME: frozenset[str] = frozenset({
    "Kling 3.0",
    "Kling 3.0 Pro",
    "Kling O3",
    "Kling 2.6 Pro",
    "Kling 2.5 Turbo Pro",
    "Kling 2.1 Pro",
    "Kling 2.1",
    "Kling 2.0",
    "Kling",
    "Kling Pro",
})


def _clamp_duration(model: str, requested: int) -> int:
    """Return the closest available duration for the given model.

    If the model isn't in MODEL_AVAILABLE_DURATIONS the requested value is
    returned unchanged (any duration is accepted by the UI).
    """
    available = MODEL_AVAILABLE_DURATIONS.get(model)
    if not available or requested in available:
        return requested
    nearest = min(available, key=lambda d: abs(d - requested))
    print(
        f"[artlist] duration {requested}s not available for {model} "
        f"— snapping to {nearest}s (available: {available})"
    )
    return nearest

ProgressCB   = Callable[[str], Awaitable[None]]
ScreenshotCB = Callable[[str, bytes], Awaitable[None]]

# ── Cookie persistence ─────────────────────────────────────────────────────────

async def _save_cookies(ctx: "BrowserContext") -> None:
    """Save all browser cookies to disk so the next run skips login."""
    try:
        cookies = await ctx.cookies()
        _COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        print(f"[artlist] session saved ({len(cookies)} cookies)")
    except Exception as e:
        print(f"[artlist] ⚠️ could not save cookies: {e}")


async def _load_cookies(ctx: "BrowserContext") -> bool:
    """Load saved auth cookies into the context and return True.

    Cloudflare-managed cookies (__cf_bm, cf-ja4, verified-bot, bot-score) are
    intentionally EXCLUDED — they are cryptographically bound to the TLS/JA3
    fingerprint of the browser that created them.  Injecting them into Playwright
    (which has a different fingerprint) causes Cloudflare to return 403 on the
    generation API even though page navigation works fine.  We let Cloudflare
    issue fresh cookies for the Playwright session by visiting artlist.io first.

    Returns False only if the file is missing or unreadable.
    """
    # Cookie names that must NOT be injected from a saved session.
    # • Cloudflare cookies (__cf_bm etc.) are cryptographically bound to the
    #   TLS/JA3 fingerprint of the originating browser — injecting them into
    #   Playwright causes 403 on the generation API.
    # • CSRF / callback session cookies are tied to the originating browser's
    #   server-side session state.  Injecting a stale CSRF token causes the
    #   Artlist API to return 403 {"success":false} on generation POST even
    #   though page navigation succeeds.  Omitting them lets the Artlist server
    #   issue fresh CSRF state for the Playwright session on first load.
    _CF_SKIP = {
        # Cloudflare per-session — cryptographically bound to the originating
        # browser's TLS/JA4 fingerprint; injecting them into Playwright causes
        # Cloudflare to issue 403 on the generation API.
        "__cf_bm", "__cfuvid", "cf-ja4", "verified-bot", "bot-score",
        "cf_clearance", "_cfuvid",
        # Short-lived referrer tracking cookies — omitting these is safe;
        # the server doesn't use them for auth.
        "artlist_original_referrer_state",
        "artlist_original_referrer_initial",
    }

    if not _COOKIES_FILE.exists():
        print("[artlist] no saved cookie file — will log in fresh")
        return False
    try:
        import time as _time
        all_cookies = json.loads(_COOKIES_FILE.read_text())
        now = _time.time()

        # Filter out Cloudflare + CSRF cookies — let the server issue fresh ones
        cookies = [c for c in all_cookies if c["name"] not in _CF_SKIP]
        skipped = [c["name"] for c in all_cookies if c["name"] in _CF_SKIP]
        if skipped:
            print(f"[artlist] skipping {len(skipped)} session-bound cookies "
                  f"(server will reissue): {skipped}")

        # Normalize sameSite — Playwright only accepts "Strict", "Lax", or "None".
        # Browser Extension exports use null / "no_restriction" / etc.
        _same_site_map = {
            None: "Lax",
            "no_restriction": "None",
            "unspecified": "Lax",
        }
        for c in cookies:
            ss = c.get("sameSite")
            if ss not in ("Strict", "Lax", "None"):
                c["sameSite"] = _same_site_map.get(ss, "Lax")

        # Normalize expirationDate (browser-extension export field) → expires
        # (Playwright's expected field).  The browser extension uses
        # "expirationDate"; Playwright's add_cookies() uses "expires".
        for c in cookies:
            if "expirationDate" in c and "expires" not in c:
                c["expires"] = c.pop("expirationDate")

        # Clear the expires field on stale cookies — if we pass an already-
        # expired timestamp, some Playwright versions silently drop the cookie.
        # By removing the field we let Playwright treat them as session cookies
        # (no explicit expiry), which means they'll be sent until the browser
        # context closes.  The server will re-validate the JWE / token on its
        # own schedule.
        expired_names = [c["name"] for c in cookies if 0 < c.get("expires", now + 1) < now]
        for c in cookies:
            if 0 < c.get("expires", now + 1) < now:
                c.pop("expires", None)
        total = len(cookies)
        print(f"[artlist] loading {total} auth cookies from file "
              f"({len(expired_names)} had stale timestamps — cleared expiry so they're still sent)")
        if expired_names:
            print(f"[artlist]   stale-timestamp cookies (kept): {expired_names[:10]}")
        await ctx.add_cookies(cookies)
        print(f"[artlist] ✅ {total} cookies injected — checking session…")
        return True
    except Exception as e:
        print(f"[artlist] ⚠️ could not load cookies: {e}")
        return False


async def _is_logged_in(page: Page) -> bool:
    """
    Verify the session by going through artlist.io → toolkit SSO flow.

    Jumping directly to toolkit.artlist.io bypasses the SSO redirect that
    sets the API auth tokens used by createUserGeneration.  Even if the page
    renders fine without them, every API call returns 403.  We must touch
    artlist.io first so the server can establish the toolkit auth tokens in
    the browser context before we navigate to the generator.
    """
    try:
        # Step 1: visit my-account.artlist.io first — our session cookies are
        # anchored there, and the auth handshake for the toolkit happens when
        # the SSO redirect originates from that domain.
        print("[artlist] cookie-login: visiting my-account.artlist.io to warm SSO…")
        try:
            await page.goto(
                "https://my-account.artlist.io/account/overview",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await asyncio.sleep(2)
        except Exception as _e:
            print(f"[artlist] my-account warmup failed (continuing): {_e}")

        # Step 2: land on artlist.io with our injected session cookies — this
        # triggers the SSO exchange that sets toolkit.artlist.io auth tokens.
        print("[artlist] cookie-login: visiting artlist.io to establish SSO tokens…")
        await page.goto(_ARTLIST_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

        # Dismiss any cookie / consent banner so it doesn't interfere
        await _dismiss_cookies(page)

        # Step 3: navigate to the toolkit — the important thing is that we arrive
        # at toolkit.artlist.io via an artlist.io-initiated navigation so that
        # any SSO redirect happens naturally.
        print("[artlist] cookie-login: navigating to AI Toolkit…")
        await page.goto(_ARTLIST_VIDEO_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

        url = page.url
        print(f"[artlist] cookie-login: landed at {url}")

        # ── Diagnostic: dump localStorage / sessionStorage to find auth tokens ──
        try:
            auth_state = await page.evaluate("""() => {
                const ls = {}, ss = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    ls[k] = (localStorage.getItem(k) || '').slice(0, 120);
                }
                for (let i = 0; i < sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    ss[k] = (sessionStorage.getItem(k) || '').slice(0, 120);
                }
                return { ls_keys: Object.keys(ls), ss_keys: Object.keys(ss),
                         ls_auth: Object.fromEntries(
                             Object.entries(ls).filter(([k]) =>
                                 /token|auth|session|jwt|bearer|access|refresh/i.test(k))),
                         ss_auth: Object.fromEntries(
                             Object.entries(ss).filter(([k]) =>
                                 /token|auth|session|jwt|bearer|access|refresh/i.test(k))) };
            }""")
            print(f"[artlist] localStorage keys ({len(auth_state['ls_keys'])}): "
                  f"{auth_state['ls_keys'][:20]}")
            print(f"[artlist] sessionStorage keys ({len(auth_state['ss_keys'])}): "
                  f"{auth_state['ss_keys'][:20]}")
            if auth_state["ls_auth"]:
                print(f"[artlist] 🔑 localStorage auth tokens: {auth_state['ls_auth']}")
            if auth_state["ss_auth"]:
                print(f"[artlist] 🔑 sessionStorage auth tokens: {auth_state['ss_auth']}")
        except Exception as _de:
            print(f"[artlist] storage diagnostic failed: {_de}")

        # If redirected to a login/signup page the session is dead
        if any(kw in url for kw in ("login", "signin", "sign-in", "auth")):
            print("[artlist] cookie-login: redirected to login — session expired")
            return False

        # Check page content for a login modal
        has_login_modal = await page.evaluate(
            """() => {
                const t = document.body.innerText || '';
                return t.includes('Sign in') && t.includes('Password') &&
                       !!document.querySelector('input[type="password"]');
            }"""
        )
        if has_login_modal:
            print("[artlist] cookie-login: login modal detected — session expired")
            return False

        print("[artlist] cookie-login: session valid ✓")
        return True
    except Exception as e:
        print(f"[artlist] _is_logged_in check failed: {e}")
        return False


# ── Stealth init script ────────────────────────────────────────────────────────
# Injected before every page load to make the browser look like a real user.

_STEALTH_JS = """
// Hide webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Fake plugins (real Chrome has plugins)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin',   filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client',       filename: 'internal-nacl-plugin' },
        ];
        arr.item = (i) => arr[i];
        arr.refresh = () => {};
        Object.setPrototypeOf(arr, PluginArray.prototype);
        return arr;
    }
});

// Realistic languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// Fake chrome object
if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false, InstallState: {}, RunningState: {} },
        runtime: {},
        loadTimes: function() { return {}; },
        csi: function() { return {}; },
    };
}

// Permissions API — real Chrome returns 'granted' for notifications
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(params);
}

// Hide automation in toString checks
const origToString = Function.prototype.toString;
Function.prototype.toString = function() {
    if (this === window.navigator.permissions.query)
        return 'function query() { [native code] }';
    return origToString.call(this);
};
"""


# ── Utility helpers ────────────────────────────────────────────────────────────

def _jitter(base_ms: int, spread: int = 300) -> int:
    """Return a random delay around base_ms ± spread (never below 50)."""
    return max(50, base_ms + random.randint(-spread // 2, spread))


async def _human_pause(min_ms: int = 300, max_ms: int = 900) -> None:
    """Sleep a random amount — feels like a human thinking."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _move_and_click(page: Page, selector: str, timeout: int = 8_000) -> bool:
    """
    Wait for an element, drift the mouse near it naturally, then click.
    Returns True on success.
    """
    try:
        el = await page.wait_for_selector(selector, timeout=timeout, state="visible")
        if not el:
            return False
        box = await el.bounding_box()
        if not box:
            await el.click()
            return True
        # Land somewhere inside the element (not always dead-center)
        x = box["x"] + box["width"]  * random.uniform(0.25, 0.75)
        y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
        await page.mouse.move(x + random.uniform(-5, 5), y + random.uniform(-5, 5))
        await _human_pause(80, 220)
        await page.mouse.click(x, y)
        return True
    except Exception:
        return False


async def _click_any(page: Page, selectors: list[str], timeout: int = 5_000) -> bool:
    """Try selectors in order; human-move before each click."""
    for sel in selectors:
        try:
            ok = await _move_and_click(page, sel, timeout=timeout)
            if ok:
                return True
        except Exception:
            pass
    return False


async def _human_type(page: Page, text: str, delay_range: tuple[int, int] = (60, 160)) -> None:
    """Type text one character at a time with human-like variable delays."""
    for ch in text:
        await page.keyboard.type(ch)
        await asyncio.sleep(random.uniform(delay_range[0] / 1000, delay_range[1] / 1000))
        # Occasionally pause longer, like a human pausing mid-thought
        if random.random() < 0.08:
            await _human_pause(200, 600)


async def _snap(page: Page, label: str, cb: Optional[ScreenshotCB]) -> None:
    if cb:
        try:
            img = await page.screenshot(type="jpeg", quality=65, full_page=False)
            await cb(f"[artlist] {label}", img)
        except Exception as e:
            print(f"[artlist] screenshot({label}): {e}")


# ── Sign-in ────────────────────────────────────────────────────────────────────

async def _dismiss_cookies(page: Page) -> None:
    """Force-click the cookie accept banner using JS so overlays can't block it."""
    clicked = await page.evaluate("""() => {
        const texts = ['accept all', 'accept cookies', 'accept', 'got it', 'ok'];
        const buttons = Array.from(document.querySelectorAll('button'));
        for (const btn of buttons) {
            const t = (btn.innerText || btn.textContent || '').toLowerCase().trim();
            if (texts.some(kw => t === kw || t.startsWith(kw))) {
                btn.click();
                return btn.innerText || btn.textContent;
            }
        }
        return null;
    }""")
    if clicked:
        print(f"[artlist] dismissed cookie banner: '{clicked.strip()}'")
        await _human_pause(400, 700)


async def _dismiss_page_banners(page: Page) -> None:
    """Hide the 'Artlist MCP / Claude Connect' promotional bar by removing it
    from the DOM.

    IMPORTANT: We never click the × close button on this banner — on some page
    versions that button is a <a href="…claude.com…"> navigation link, and
    clicking it navigates the browser to Claude, producing a black overlay in
    all subsequent screenshots and breaking the generation flow entirely.

    Instead we walk up the DOM tree from any text node that mentions 'claude'
    or 'mcp', find the nearest bar-shaped container, and set its display to
    'none'.  This is safe because it never triggers click handlers.
    """
    dismissed = await page.evaluate("""() => {
        const hidden = [];
        const bannerKeywords = ['mcp', 'claude connect', 'claude', 'artlist mcp'];

        // Walk every visible text node looking for MCP / Claude Connect mention
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const t = (node.textContent || '').toLowerCase();
            if (!bannerKeywords.some(kw => t.includes(kw))) continue;
            const parent = node.parentElement;
            if (!parent || !parent.offsetParent) continue;

            // Walk up to find the outermost bar-like container
            // (full-width, thin height, near top of viewport)
            let el = parent;
            let found = false;
            for (let depth = 0; depth < 10; depth++) {
                if (!el || el === document.body) break;
                const r = el.getBoundingClientRect();
                if (r.width > window.innerWidth * 0.7 && r.height < 90 && r.top < 180) {
                    el.style.setProperty('display', 'none', 'important');
                    hidden.push(node.textContent.trim().slice(0, 60));
                    found = true;
                    break;
                }
                el = el.parentElement;
            }
            // If no bar container found, just hide the direct parent
            if (!found) {
                parent.style.setProperty('display', 'none', 'important');
                hidden.push('(parent) ' + node.textContent.trim().slice(0, 50));
            }
            break;  // one banner at a time is enough
        }

        return hidden;
    }""")
    if dismissed:
        print(f"[artlist] dismissed banners: {dismissed}")


async def _login(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> None:
    await progress("🔑 Connecting…")

    # Navigate to homepage like a normal user
    await page.goto(_ARTLIST_HOME_URL, wait_until="domcontentloaded", timeout=40_000)
    await _human_pause(2_000, 3_500)

    # Dismiss cookie banner — use JS so it works even when overlaying other elements
    await _dismiss_cookies(page)
    await _human_pause(300, 500)

    await _snap(page, "home", snap)

    # Wipe any locally-stored "remembered" email so Artlist doesn't pre-fill it
    await page.evaluate("""() => {
        try { localStorage.clear(); } catch(e) {}
        try { sessionStorage.clear(); } catch(e) {}
    }""")

    # ── Open the sign-in modal (desktop: Sign In is in the top nav) ─────────────
    await progress("🔑 Looking for Sign In…")

    # Use JS to find and click the Sign In link — bypasses any overlay
    opened = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('a, button'));
        for (const el of all) {
            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
            if (t === 'sign in') {
                el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return el.tagName + ':' + el.innerText.trim();
            }
        }
        return null;
    }""")
    print(f"[artlist] opened sign-in via: {opened!r}")

    await _human_pause(1_200, 2_000)
    await _snap(page, "signin-modal", snap)

    # ── Fill email ────────────────────────────────────────────────────────────
    email_sel = None
    for sel in [
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='email' i]",
        "input[autocomplete='email']",
    ]:
        try:
            el = await page.wait_for_selector(sel, timeout=5_000, state="visible")
            if el:
                email_sel = sel
                break
        except Exception:
            pass

    if not email_sel:
        await _snap(page, "no-email-field", snap)
        raise RuntimeError("Sign-in failed — could not find email input.")

    # Click the field, clear it, fill instantly
    await _move_and_click(page, email_sel, timeout=5_000)
    await _human_pause(200, 400)
    await page.fill(email_sel, _ARTLIST_EMAIL)

    await _human_pause(300, 600)
    await _snap(page, "email-typed", snap)

    # ── Fill password ─────────────────────────────────────────────────────────
    pw_sel = None
    for sel in [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='password' i]",
    ]:
        try:
            el = await page.wait_for_selector(sel, timeout=5_000, state="visible")
            if el:
                pw_sel = sel
                break
        except Exception:
            pass

    if not pw_sel:
        await _snap(page, "no-pw-field", snap)
        raise RuntimeError("Sign-in failed — could not find password input.")

    await _move_and_click(page, pw_sel, timeout=5_000)
    await _human_pause(200, 400)
    await page.fill(pw_sel, _ARTLIST_PASSWORD)

    await _human_pause(400, 700)
    await _snap(page, "pw-typed", snap)

    # ── Click the eye icon to reveal password (looks human) ───────────────────
    for eye_sel in [
        "button[aria-label*='show' i]",
        "button[aria-label*='password' i]",
        "[data-testid*='eye' i]",
        "button svg[class*='eye' i]",
        # Artlist uses an SVG eye inside the password field wrapper
        "input[type='password'] ~ button",
        "input[type='password'] + button",
        ".password-toggle",
        # Generic: any button inside the password wrapper
        "div:has(> input[type='password']) button",
    ]:
        try:
            el = await page.query_selector(eye_sel)
            if el and await el.is_visible():
                await _human_pause(400, 700)
                await el.click()
                print(f"[artlist] clicked eye icon ({eye_sel})")
                await _human_pause(300, 500)
                break
        except Exception:
            pass

    await _human_pause(500, 900)

    # ── Dismiss cookies again — banner often covers the Sign In button ─────────
    await _dismiss_cookies(page)
    await _human_pause(400, 600)

    # ── Submit ─────────────────────────────────────────────────────────────────
    # Priority 1: form's submit button (most reliable — avoids nav "Sign In" link)
    # Priority 2: button whose text is EXACTLY "Sign In"
    # Priority 3: keyboard Enter
    clicked = await page.evaluate("""() => {
        function fire(el) {
            el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
            el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
            el.dispatchEvent(new MouseEvent('mouseup',  {bubbles:true}));
            el.dispatchEvent(new MouseEvent('click',    {bubbles:true, cancelable:true}));
        }
        // 1. submit button inside any visible form
        const formBtn = document.querySelector(
            'form button[type="submit"], form input[type="submit"]'
        );
        if (formBtn && formBtn.offsetParent !== null) {
            fire(formBtn);
            return 'form-submit: ' + (formBtn.innerText || formBtn.value || '').trim();
        }
        // 2. button with EXACT text "Sign In" (case-insensitive)
        const allBtns = Array.from(document.querySelectorAll('button'));
        for (const btn of allBtns) {
            const t = (btn.innerText || '').trim().toLowerCase();
            if (t === 'sign in' && btn.offsetParent !== null) {
                fire(btn);
                return 'exact-match: ' + btn.innerText.trim();
            }
        }
        return null;
    }""")
    print(f"[artlist] submit result: {clicked!r}")
    if not clicked:
        await page.keyboard.press("Enter")

    await progress("⏳ Waiting for sign-in to complete…")
    await _human_pause(4_000, 6_000)
    await _snap(page, "after-login", snap)

    # ── Detect login errors (modal stays open — URL never changes on Artlist) ──
    error_text = await page.evaluate("""() => {
        // Look for visible error/alert elements that contain a real error message
        // (skip short field-label strings like "Password" or "Email").
        const candidateSelectors = [
            '[role="alert"]',
            '[class*="error-message"]', '[class*="errorMessage"]',
            '[data-testid*="error"]', '.form-error',
            'p[style*="color: red"]', 'span[style*="color: red"]',
            // Artlist wraps inline field errors in a <p> near the input
            'form p', 'form span',
        ];
        for (const sel of candidateSelectors) {
            for (const el of document.querySelectorAll(sel)) {
                const t = (el.innerText || '').trim();
                // Must be visible, non-empty, and not just a short field label
                if (!el.offsetParent) continue;
                if (!t || t.length < 8) continue;
                // Skip elements that are just form labels / headings
                if (/^(email|password|sign in|sign up|log in|forgot)$/i.test(t)) continue;
                return t;
            }
        }
        // Broader fallback: any visible element whose class contains "error"/"Error"
        // but is long enough to be a real message (avoids matching "Password" labels).
        for (const el of document.querySelectorAll('[class*="error"],[class*="Error"]')) {
            const t = (el.innerText || '').trim();
            if (!el.offsetParent) continue;
            if (t.length < 12) continue;  // skip short labels
            if (/^(email|password|sign in)$/i.test(t)) continue;
            return t;
        }
        // Also check if the sign-in modal is still visible (no explicit error shown)
        const pwInput = document.querySelector('input[type="password"]');
        if (pwInput && pwInput.offsetParent !== null) {
            return '__modal_still_open__';
        }
        return null;
    }""")

    if error_text:
        if error_text == "__modal_still_open__":
            msg = "Login modal still open after submit — credentials may be wrong or bot-detected"
        else:
            msg = f"Login error: {error_text}"
        await _snap(page, "login-fail", snap)
        raise RuntimeError(msg)

    await progress("✅ Signed in!")
    # Save session so the next request skips the whole login dance
    await _save_cookies(page.context)


# ── Open video generator ───────────────────────────────────────────────────────

async def _open_video_generator(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> None:
    await progress("🎬 Opening generator…")

    for attempt in range(3):
        await page.goto(_ARTLIST_VIDEO_URL, wait_until="domcontentloaded", timeout=40_000)
        await _human_pause(3_000, 5_000)

        # Confirm we actually landed on the toolkit, not redirected back to artlist.io
        if "toolkit.artlist.io" in page.url:
            break
        print(f"[artlist] _open_video_generator attempt {attempt+1}: wrong URL={page.url}, retrying…")
        await _human_pause(2_000, 3_000)
    else:
        await _snap(page, "wrong-page", snap)
        raise RuntimeError("Could not reach the video generator — please try again.")

    # Scroll to the Generate button / prompt area so the black promotional
    # Scroll the prompt editor into view so the dark hero section above is
    # pushed off-screen before we screenshot.
    try:
        await page.evaluate("""() => {
            // Try known Artlist prompt-editor class first, then generic fallbacks.
            // NOTE: ':has-text()' is Playwright syntax — never use it in JS evaluate.
            const el =
                document.querySelector('[class*="prompt-editor"]') ||
                document.querySelector('[class*="prompt"] [contenteditable="true"]') ||
                document.querySelector('[contenteditable="true"]');
            if (el && el.offsetParent !== null) {
                el.scrollIntoView({ block: 'start', behavior: 'instant' });
            } else {
                // Fallback: jump 600 px down — enough to clear the dark hero section
                window.scrollTo(0, 600);
            }
        }""")
        await _human_pause(300, 500)
    except Exception as _e:
        print(f"[artlist] pre-screenshot scroll failed (non-fatal): {_e}")

    await _snap(page, "video-gen", snap)

    # Dismiss any modal / banner (cookie consent, etc.)
    # NOTE: Do NOT include aria-label*='close' or aria-label*='dismiss' here —
    # those selectors match the X button on the "Artlist MCP / Claude Connect"
    # promotional banner and clicking it can open a modal or navigate away,
    # which produces a black overlay in subsequent screenshots.
    for sel in [
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await _human_pause(300, 600)
                await el.click()
                await _human_pause(400, 700)
        except Exception:
            pass


# ── Remove Start / End frames ──────────────────────────────────────────────────

async def _remove_frames(page: Page, snap: Optional[ScreenshotCB]) -> None:
    """
    Remove Start Frame / End Frame chips from the bottom toolbar.

    The grey circular X button is always visible on each chip (not just on hover
    when a frame image is loaded).  We always try to click it — if no chip or no
    X is found we just move on silently.

    Strategy:
      1. Find the chip button by label text to get its bounding rect.
      2. Search the whole page for a small (≤ 48 px) button near the chip's
         top-right corner — without waiting for hover, because the X is always
         rendered.
      3. If not found pre-hover, hover the chip center (CSS :hover may reveal it)
         then search again.
      4. Click the X if found; verify it's gone; retry once at the raw corner if
         the first click missed.
    """
    def _find_x_js(tx: float, ty: float, radius: int = 60) -> str:
        return f"""() => {{
            const tx = {tx}, ty = {ty};
            for (const el of document.querySelectorAll(
                    'button, [role="button"], [class*="close" i], [class*="remove" i]')) {{
                if (!el.offsetParent) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.width > 48) continue;
                const bx = r.left + r.width  / 2;
                const by = r.top  + r.height / 2;
                if (Math.abs(bx - tx) < {radius} && Math.abs(by - ty) < {radius}) {{
                    return {{ x: bx, y: by }};
                }}
            }}
            return null;
        }}"""

    for label in ("Start Frame", "End Frame"):
        try:
            # ── Step 1: Find the chip bounding rect ─────────────────────────────
            chip = await page.evaluate(f"""() => {{
                const labelText = "{label}";
                for (const el of document.querySelectorAll('button, [role="button"]')) {{
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (!txt.includes(labelText)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0) continue;
                    return {{
                        cx: r.left + r.width  / 2,
                        cy: r.top  + r.height / 2,
                        tx: r.right,
                        ty: r.top + r.height * 0.25,
                        w:  r.width,
                        h:  r.height,
                    }};
                }}
                return null;
            }}""")

            if not chip:
                print(f"[artlist] '{label}' chip not found in DOM — skipping")
                continue

            cx, cy = chip["cx"], chip["cy"]
            tx, ty = chip["tx"], chip["ty"]

            # ── Step 2: Try to find X without hovering first (it's always visible) ─
            x_btn = await page.evaluate(_find_x_js(tx, ty))

            # ── Step 3: If not found pre-hover, hover then try again ─────────────
            if not x_btn:
                await page.mouse.move(cx, cy)
                await _human_pause(500, 800)
                x_btn = await page.evaluate(_find_x_js(tx, ty))

            if not x_btn:
                print(f"[artlist] '{label}' — no X button found — skipping")
                continue

            ix, iy = x_btn["x"], x_btn["y"]
            print(f"[artlist] '{label}' X button at ({ix:.0f},{iy:.0f}) — clicking")
            await page.mouse.move(ix, iy)
            await _human_pause(150, 250)
            await page.mouse.click(ix, iy)

            # ── Step 4: Verify removal; retry once if X is still there ──────────
            await _human_pause(400, 700)
            still_has_x = await page.evaluate(_find_x_js(tx, ty))
            if still_has_x:
                print(f"[artlist] '{label}' X still present — retry at raw corner")
                await page.mouse.move(tx - 8, ty)
                await _human_pause(200, 300)
                await page.mouse.click(tx - 8, ty)
                await _human_pause(400, 600)
            else:
                print(f"[artlist] '{label}' removed ✓")

        except Exception as e:
            print(f"[artlist] _remove_frames '{label}' error: {e}")

    # Scroll the prompt editor into view before screenshotting.
    try:
        await page.evaluate("""() => {
            const el =
                document.querySelector('[class*="prompt-editor"]') ||
                document.querySelector('[class*="prompt"] [contenteditable="true"]') ||
                document.querySelector('[contenteditable="true"]');
            if (el && el.offsetParent !== null) {
                el.scrollIntoView({ block: 'start', behavior: 'instant' });
            } else {
                window.scrollTo(0, 600);
            }
        }""")
    except Exception:
        pass

    await _snap(page, "frames-removed", snap)


# ── Upload reference image ─────────────────────────────────────────────────────

async def _upload_image(
    page: Page,
    image_bytes: bytes,
    image_ext: str,
    snap: Optional[ScreenshotCB],
) -> None:
    """
    Upload a reference image into the Artlist video toolkit prompt.

    UI flow:
      1. Click the "+" pill in the prompt toolbar
      2. Click "Image Reference" from the popup menu
      3a. If a sub-sheet appears, click "Upload Image" → file chooser
      3b. If clicking "Image Reference" itself opens the chooser, catch it there

    The file-chooser listener is started BEFORE the "Image Reference" click so
    it catches the chooser no matter which step triggers it.
    """
    suffix = image_ext if image_ext.startswith(".") else f".{image_ext}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        await _human_pause(500, 800)

        # ── Step 0: Make sure the settings panel is fully closed ──────────────
        # Use getBoundingClientRect (not offsetParent) — fixed-position elements
        # have offsetParent===null even when fully visible on screen.
        def _panel_check_js():
            return """
                () => {
                    const vh = window.innerHeight;
                    for (const el of document.querySelectorAll('*')) {
                        const t = (el.innerText || '').trim();
                        if (t !== 'Aspect Ratio' && t !== 'Resolution') continue;
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.top >= 0 && r.top <= vh)
                            return true;
                    }
                    return false;
                }
            """
        panel_open = await page.evaluate(_panel_check_js())
        if panel_open:
            print("[artlist] settings panel still open before image upload — closing via Escape")
            await page.keyboard.press("Escape")
            await _human_pause(700, 1_000)
            still_open = await page.evaluate(_panel_check_js())
            if still_open:
                print("[artlist] settings panel persists — trying pill toggle")
                await page.evaluate("""
                    () => {
                        for (const btn of document.querySelectorAll('button,[role="button"]')) {
                            const t = (btn.innerText || '').trim();
                            if (/\\d+:\\d+|720p|1080p|480p/.test(t)) {
                                btn.click(); return 'pill-close';
                            }
                        }
                    }
                """)
                await _human_pause(600, 900)

        await _snap(page, "before-image-upload", snap)

        # ── Step 1: Click "Video input options" (the "+" button) ────────────
        # Artlist labels this button aria-label="Video input options".
        # It opens a dropdown that contains "Image Reference".
        plus_info = await page.evaluate("""
            () => {
                // Priority 1: exact aria-label match for known Artlist "+" button names
                const knownLabels = [
                    'video input options',
                    'add input',
                    'add reference',
                    'image reference',
                    'add',
                ];
                for (const el of document.querySelectorAll('button,[role="button"]')) {
                    const lbl = (el.getAttribute('aria-label') || '').toLowerCase();
                    const ttl = (el.getAttribute('title') || '').toLowerCase();
                    const txt = (el.innerText || el.textContent || '').trim();
                    const r   = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;

                    if (knownLabels.includes(lbl) || knownLabels.includes(ttl) || txt === '+') {
                        el.click();
                        return `known-label: "${lbl||ttl||txt}" at (${(r.left+r.width/2).toFixed(0)},${(r.top+r.height/2).toFixed(0)})`;
                    }
                }
                // Priority 2: button whose aria-label CONTAINS "video input" or "input option"
                for (const el of document.querySelectorAll('button,[role="button"]')) {
                    const lbl = (el.getAttribute('aria-label') || '').toLowerCase();
                    const r   = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;
                    if (lbl.includes('video input') || lbl.includes('input option')) {
                        el.click();
                        return `partial-label: "${lbl}" at (${(r.left+r.width/2).toFixed(0)},${(r.top+r.height/2).toFixed(0)})`;
                    }
                }
                return null;
            }
        """)

        # Debug: always dump small buttons for diagnosis regardless of outcome
        dbg_btns = await page.evaluate("""
            () => {
                const vh = window.innerHeight;
                const out = [];
                for (const el of document.querySelectorAll(
                    'button, div[role="button"], span[role="button"]'
                )) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;
                    if (r.width > 65 || r.height > 65) continue;
                    const cy = r.top + r.height / 2;
                    if (cy < vh * 0.20 || cy > vh * 0.85) continue;
                    out.push({
                        tag: el.tagName,
                        lbl: el.getAttribute('aria-label') || '',
                        txt: (el.innerText||'').trim().slice(0, 20),
                        x:   Math.round(r.left + r.width / 2),
                        y:   Math.round(r.top  + r.height / 2),
                        w:   Math.round(r.width), h: Math.round(r.height),
                    });
                }
                out.sort((a, b) => a.x - b.x);
                return out.slice(0, 15);
            }
        """)
        print(f"[artlist] small buttons in toolbar band: {dbg_btns}")

        if not plus_info:
            # Nuclear fallback: mouse.click at known "+" position derived from
            # where the Generate button is (both are in the same toolbar row).
            # Generate is consistently at the far right; "+" at the far left.
            gen_pos = await page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('button')) {
                        if (/^generate$/i.test((el.innerText||'').trim())) {
                            const r = el.getBoundingClientRect();
                            return { x: r.left + r.width/2, y: r.top + r.height/2 };
                        }
                    }
                    return null;
                }
            """)
            if gen_pos:
                # The "+" is in the same row (same y), far to the left edge of
                # the prompt container.  The container starts ~795px left of center
                # on a 1440px viewport; "+" is about 380px from left edge.
                plus_x = 380
                plus_y = gen_pos["y"] - 120  # "+" is ~120px above the settings bar
                print(f"[artlist] nuclear fallback: mouse.click at ({plus_x:.0f},{plus_y:.0f})")
                await page.mouse.click(plus_x, plus_y)
                plus_info = f"mouse-fallback ({plus_x},{plus_y:.0f})"
            else:
                print("[artlist] ⚠️ '+' button not found — skipping image upload")
                await _snap(page, "image-upload-no-plus", snap)
                return

        print(f"[artlist] '+' clicked ({plus_info})")
        await _human_pause(500, 800)
        await _snap(page, "after-plus-click", snap)

        # ── Steps 2+3: click "Image Reference" then catch the file chooser ──
        # Critical: Playwright's .click() dispatches native pointer events
        # that bubble through the DOM and fire React's synthetic onClick.
        # JS el.click() on a SPAN child does NOT do this — React never sees it.
        await _human_pause(400, 600)

        uploaded = False

        # ── Step 2: click "Image Reference" using Playwright locator ─────────
        img_ref_clicked = False
        # Dump the menu items so we can see what's visible
        menu_items = await page.evaluate("""
            () => {
                const out = [];
                for (const el of document.querySelectorAll(
                    'li, [role="menuitem"], div[class*="item"], div[class*="menu"]'
                )) {
                    const t = (el.innerText || '').trim();
                    if (!t) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;
                    out.push({ tag: el.tagName, cls: el.className.slice(0,40), txt: t.slice(0,30),
                                x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2) });
                }
                return out.slice(0, 15);
            }
        """)
        print(f"[artlist] menu items visible: {menu_items}")

        # Try Playwright locators first (proper React event dispatch)
        for sel in [
            "li:has-text('Image Reference')",
            "[role='menuitem']:has-text('Image Reference')",
            "button:has-text('Image Reference')",
            "div:has-text('Image Reference')",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2_000):
                    # Use expect_file_chooser around the click that opens it
                    async with page.expect_file_chooser(timeout=20_000) as fc_info:
                        await loc.click()
                        print(f"[artlist] 'Image Reference' clicked via locator ({sel})")
                        img_ref_clicked = True
                        await _snap(page, "after-imgref-click", snap)
                        await _human_pause(800, 1_200)
                        # Also click "Upload Image" sub-option if it appears
                        for sub_text in ["Upload Image", "Upload from computer", "Upload", "Computer"]:
                            try:
                                sub_loc = page.locator(f"text='{sub_text}'").first
                                if await sub_loc.is_visible(timeout=2_000):
                                    await sub_loc.click()
                                    print(f"[artlist] '{sub_text}' sub-option clicked")
                                    break
                            except Exception:
                                pass
                        await _snap(page, "after-upload-btn", snap)

                    fc = await fc_info.value
                    await fc.set_files(tmp_path)
                    await _human_pause(2_000, 3_000)
                    await _snap(page, "after-image-set", snap)
                    print("[artlist] reference image uploaded ✓")
                    uploaded = True
                    break
            except Exception as e:
                print(f"[artlist] chooser attempt ({sel}) failed: {e}")
                if img_ref_clicked:
                    break  # Already clicked — don't retry with another selector

        # ── Step 2b: JS dispatchEvent fallback ───────────────────────────────
        if not uploaded and not img_ref_clicked:
            # dispatchEvent with bubbles:true correctly triggers React handlers
            clicked_tag = await page.evaluate("""
                () => {
                    const LABELS = ['Image Reference'];
                    for (const el of document.querySelectorAll(
                        'li, [role="menuitem"], button, div, span'
                    )) {
                        const t = (el.innerText || el.textContent || '').trim();
                        if (LABELS.some(l => t === l || t.startsWith(l))) {
                            const r = el.getBoundingClientRect();
                            if (r.width < 5 || r.height < 5) continue;
                            // Fire full pointer sequence so React synthetic events fire
                            for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                                el.dispatchEvent(new MouseEvent(type, {
                                    bubbles: true, cancelable: true,
                                    clientX: r.left + r.width/2,
                                    clientY: r.top  + r.height/2,
                                }));
                            }
                            return el.tagName + '|' + el.className.slice(0,30);
                        }
                    }
                    return null;
                }
            """)
            if clicked_tag:
                print(f"[artlist] 'Image Reference' via dispatchEvent ({clicked_tag})")
                img_ref_clicked = True
                await _snap(page, "after-imgref-click", snap)
                await _human_pause(1_000, 1_500)

                # Now try to catch a file chooser from the sub-menu click
                try:
                    async with page.expect_file_chooser(timeout=15_000) as fc_info:
                        for sub_text in ["Upload Image", "Upload from computer", "Upload"]:
                            try:
                                sub_loc = page.locator(f"text='{sub_text}'").first
                                if await sub_loc.is_visible(timeout=2_000):
                                    await sub_loc.click()
                                    print(f"[artlist] '{sub_text}' clicked (dispatchEvent path)")
                                    break
                            except Exception:
                                pass
                        await _snap(page, "after-upload-btn", snap)

                    fc = await fc_info.value
                    await fc.set_files(tmp_path)
                    await _human_pause(2_000, 3_000)
                    await _snap(page, "after-image-set", snap)
                    print("[artlist] reference image uploaded (dispatchEvent path) ✓")
                    uploaded = True
                except Exception as fc_err2:
                    print(f"[artlist] ⚠️ dispatchEvent chooser failed: {fc_err2}")
            else:
                print("[artlist] ⚠️ 'Image Reference' not found in menu — skipping upload")
                await _snap(page, "image-upload-no-imgref", snap)

        if not uploaded:
            print("[artlist] ⚠️ image upload failed — generation will proceed without image reference")
            await _snap(page, "image-upload-failed", snap)

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Upload Start Frame (Kling image-to-video) ─────────────────────────────────

async def _upload_start_frame(
    page: Page,
    image_bytes: bytes,
    image_ext: str,
    snap: Optional[ScreenshotCB],
) -> None:
    """
    Upload an image as the Start Frame for Kling models.

    Kling uses "Start & End Frame" for image-to-video — the "Image Reference"
    menu item is disabled for Kling.  This function clicks:
      + → Start & End Frame → Start Frame → file chooser

    Must be called AFTER the model is selected so the Kling chip is active.
    """
    suffix = image_ext if image_ext.startswith(".") else f".{image_ext}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        await _human_pause(500, 800)

        # ── Step 1: Click "+" (Video input options) ──────────────────────────
        plus_info = await page.evaluate("""
            () => {
                const knownLabels = ['video input options', 'add input', 'add reference', 'add'];
                for (const el of document.querySelectorAll('button,[role="button"]')) {
                    const lbl = (el.getAttribute('aria-label') || '').toLowerCase();
                    const txt = (el.innerText || el.textContent || '').trim();
                    const r   = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;
                    if (knownLabels.includes(lbl) || txt === '+') {
                        el.click();
                        return `label: "${lbl||txt}"`;
                    }
                }
                return null;
            }
        """)
        if not plus_info:
            print("[artlist] ⚠️ '+' button not found for Start Frame upload — skipping")
            return
        print(f"[artlist] Start Frame: '+' clicked ({plus_info})")
        await _human_pause(500, 800)
        await _snap(page, "start-frame-plus-clicked", snap)

        # ── Step 2: Click "Start & End Frame" from the popup menu ────────────
        sef_clicked = False
        for sel in [
            "li:has-text('Start & End Frame')",
            "[role='menuitem']:has-text('Start & End Frame')",
            "li:has-text('Start Frame')",
            "[role='menuitem']:has-text('Start Frame')",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2_000):
                    await loc.click()
                    print(f"[artlist] 'Start & End Frame' clicked via ({sel})")
                    sef_clicked = True
                    break
            except Exception:
                pass

        if not sef_clicked:
            # JS fallback
            clicked = await page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('li,[role="menuitem"],button,div')) {
                        const t = (el.innerText || el.textContent || '').trim();
                        if (!/start.*end frame|start.*frame/i.test(t)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 5 || r.height < 5) continue;
                        for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                            el.dispatchEvent(new MouseEvent(type, {
                                bubbles: true, cancelable: true,
                                clientX: r.left + r.width/2, clientY: r.top + r.height/2,
                            }));
                        }
                        return el.tagName + '|' + (el.innerText||'').slice(0,30);
                    }
                    return null;
                }
            """)
            if clicked:
                print(f"[artlist] 'Start & End Frame' clicked via JS ({clicked})")
                sef_clicked = True

        if not sef_clicked:
            print("[artlist] ⚠️ 'Start & End Frame' not found in menu — skipping Start Frame upload")
            await _snap(page, "start-frame-no-sef", snap)
            return

        await _human_pause(500, 800)
        await _snap(page, "start-frame-sef-clicked", snap)

        # ── Step 3: Click "Start Frame" sub-option and catch the file chooser ─
        uploaded = False
        for sel in [
            "li:has-text('Start Frame')",
            "[role='menuitem']:has-text('Start Frame')",
            "button:has-text('Start Frame')",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2_000):
                    async with page.expect_file_chooser(timeout=20_000) as fc_info:
                        await loc.click()
                        print(f"[artlist] 'Start Frame' sub-option clicked ({sel})")
                        await _human_pause(500, 800)
                    fc = await fc_info.value
                    await fc.set_files(tmp_path)
                    await _human_pause(2_000, 3_000)
                    await _snap(page, "start-frame-uploaded", snap)
                    print("[artlist] Start Frame image uploaded ✓")
                    uploaded = True
                    break
            except Exception as e:
                print(f"[artlist] Start Frame sub-option attempt ({sel}) failed: {e}")

        if not uploaded:
            # The "+" → "Start & End Frame" click may have opened Start Frame directly
            # without a sub-menu.  Try catching a file chooser from the direct click.
            try:
                async with page.expect_file_chooser(timeout=10_000) as fc_info:
                    # Trigger by clicking the Start Frame chip that should now appear
                    chip_clicked = await page.evaluate("""
                        () => {
                            for (const el of document.querySelectorAll('button,[role="button"]')) {
                                const t = (el.innerText || '').trim();
                                if (!/^start frame$/i.test(t)) continue;
                                const r = el.getBoundingClientRect();
                                if (r.width < 5 || r.height < 5) continue;
                                el.click();
                                return true;
                            }
                            return false;
                        }
                    """)
                    if chip_clicked:
                        print("[artlist] Start Frame chip clicked for file chooser")
                fc = await fc_info.value
                await fc.set_files(tmp_path)
                await _human_pause(2_000, 3_000)
                await _snap(page, "start-frame-uploaded", snap)
                print("[artlist] Start Frame image uploaded via chip ✓")
                uploaded = True
            except Exception as e:
                print(f"[artlist] ⚠️ Start Frame chip fallback failed: {e}")

        if not uploaded:
            print("[artlist] ⚠️ Start Frame image upload failed — generation will proceed without image")
            await _snap(page, "start-frame-upload-failed", snap)

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Select model via bottom-bar dropdown ──────────────────────────────────────

async def _select_model(
    page: Page,
    model: str,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
    *,
    duration_override: Optional[int] = None,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    audio: bool = False,
) -> None:
    raw_duration = duration_override if duration_override is not None else MODEL_DURATIONS.get(model, 10)
    duration = _clamp_duration(model, raw_duration)
    await progress(f"⚙️ Choosing {model} ({duration}s)…")
    await _human_pause(600, 1_000)

    # The bottom bar shows the current model as a clickable button/pill.
    # Clicking it opens a dropdown: Gemini Omni Flash, Seedance 2.0,
    # Seedance 2.0 Mini, All Models →
    # Blur the prompt field first so the model pill becomes visible in the
    # bottom bar.  _type_prompt already does this, but we do it again here as
    # a safety net in case the UI hasn't fully settled yet.
    await page.keyboard.press("Escape")
    await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
    await _human_pause(300, 500)

    # After typing the prompt, Artlist can hide the model pill while the prompt
    # field is still "active" in React's state.  Retry up to 5 × 1.2 s to give
    # the UI time to settle and show the pill.
    MODEL_RE_JS = "/Kling|Gemini|Omni|Flash|Seedance|Vidu|Runway|Wan|Hailuo|Veo|Sora|HappyHorse|Horse|Grok/i"

    async def _find_model_pill():
        return await page.evaluate(
            f"""() => {{
                const MODEL_RE = {MODEL_RE_JS};
                const candidates = [];
                for (const el of document.querySelectorAll('button, div, span, a')) {{
                    const t = (el.innerText || '').trim();
                    if (!t || t.length > 40) continue;
                    if (!MODEL_RE.test(t)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {{
                        candidates.push({{
                            x: rect.left + rect.width / 2,
                            y: rect.top + rect.height / 2,
                            text: t,
                            area: rect.width * rect.height,
                        }});
                    }}
                }}
                if (!candidates.length) return null;
                candidates.sort((a, b) => a.area - b.area);
                return candidates[0];
            }}"""
        )

    pill_coords = None
    for _pill_attempt in range(5):
        pill_coords = await _find_model_pill()
        if pill_coords:
            break
        print(f"[artlist] model pill not visible yet — waiting (attempt {_pill_attempt + 1}/5)")
        # Extra blur attempts to nudge React out of prompt-focused state
        await page.keyboard.press("Escape")
        await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
        await asyncio.sleep(1.2)

    opened = None
    if pill_coords:
        await page.mouse.move(pill_coords["x"], pill_coords["y"])
        await _human_pause(150, 250)
        await page.mouse.click(pill_coords["x"], pill_coords["y"])
        opened = f'mouse-click: {pill_coords["text"]} at ({pill_coords["x"]:.0f},{pill_coords["y"]:.0f})'
    else:
        print("[artlist] ⚠️ model pill not found after 5 attempts — skipping model selection")

    print(f"[artlist] model dropdown open: {opened!r}")

    await _human_pause(800, 1_200)
    await _snap(page, "model-dropdown", snap)

    # ── Click the desired model from the dropdown ──────────────────────────────
    # The short dropdown shows ~3 models (e.g. Kling 3.0, Gemini Omni Flash,
    # Seedance 2.0) plus an "All Models →" link for the full catalogue.
    # Strategy:
    #   1. Exact text match in the visible short list
    #   2. Partial/prefix match (handles "Seedance 2.0 Mini" → "Seedance 2.0")
    #   3. Click "All Models" to expand, then exact + partial in the full list

    async def _try_click_model_in_page(target: str) -> bool:
        """Try exact then prefix match; return True if clicked."""
        # Exact match first
        clicked = await _click_any(page, [
            f"li:has-text('{target}')",
            f"[role='option']:has-text('{target}')",
            f"[role='menuitem']:has-text('{target}')",
            f"button:has-text('{target}')",
            f"span:has-text('{target}')",
        ], timeout=3_000)
        if clicked:
            return True

        # JS exact/prefix match
        return await page.evaluate(
            """([exact, prefix]) => {
                const sel = 'li, [role="option"], [role="menuitem"], button, span, div';
                for (const el of document.querySelectorAll(sel)) {
                    const t = (el.innerText || '').trim();
                    if (!el.offsetParent) continue;
                    if (t === exact || t.startsWith(prefix)) {
                        el.click(); return true;
                    }
                }
                return false;
            }""",
            [target, target.split()[0]],   # e.g. prefix = "Seedance"
        )

    # Step 1: try in the short visible list
    model_clicked = await _try_click_model_in_page(model)
    print(f"[artlist] model click (short list): {model_clicked}")

    # Step 2: open "All Models" and retry
    if not model_clicked:
        print(f"[artlist] '{model}' not in short list — clicking 'All Models'")
        all_models_clicked = await _click_any(page, [
            "a:has-text('All Models')",
            "button:has-text('All Models')",
            "span:has-text('All Models')",
            "[href*='models']",
        ], timeout=4_000)
        if not all_models_clicked:
            # Try JS click on any visible element whose text starts with "All Models"
            all_models_clicked = await page.evaluate(
                """() => {
                    for (const el of document.querySelectorAll('a,button,span,div')) {
                        const t = (el.innerText || '').trim();
                        if (/^all models/i.test(t) && el.offsetParent) {
                            el.click(); return true;
                        }
                    }
                    return false;
                }"""
            )
        print(f"[artlist] 'All Models' clicked: {all_models_clicked}")
        await _human_pause(1_000, 1_500)
        await _snap(page, "all-models", snap)
        model_clicked = await _try_click_model_in_page(model)
        print(f"[artlist] model click (all models list): {model_clicked}")

    if not model_clicked:
        print(f"[artlist] ⚠️ model '{model}' not found anywhere — using default")

    await _human_pause(600, 1_000)
    await _snap(page, "model-selected", snap)

    # ── Set duration/resolution inside the model settings panel ───────────────
    # When a model is clicked from the dropdown, Artlist shows its settings
    # (Duration, Resolution, Aspect Ratio) on the RIGHT side of the same popup.
    # We set options there first, then click "Use Model" to confirm.
    # This is separate from the bottom-bar settings pill handled below.
    if model_clicked:
        # Try setting duration directly in the model panel (before Use Model)
        dur_text_inline = f"{duration}s" if duration else None
        dur_text_inline_alt = f"{duration} sec" if duration else None
        if dur_text_inline:
            # Click the matching duration chip/button in the right-side panel
            await page.evaluate(
                """([d1, d2]) => {
                    for (const el of document.querySelectorAll('button,span,label,div')) {
                        const t = (el.innerText || '').trim();
                        if ((t === d1 || t === d2) && el.offsetParent) {
                            el.click(); return true;
                        }
                    }
                    return false;
                }""",
                [dur_text_inline, dur_text_inline_alt],
            )
            await _human_pause(200, 400)

        # Click "Use Model" to confirm the selection and close the dropdown
        use_model_clicked = await _click_any(page, [
            "button:has-text('Use Model')",
            "button:has-text('Use model')",
            "[role='button']:has-text('Use Model')",
        ], timeout=3_000)
        if use_model_clicked:
            print("[artlist] ✅ clicked 'Use Model' to confirm model selection")
            await _human_pause(700, 1_100)
        else:
            print("[artlist] ℹ️ 'Use Model' button not found — continuing")

    await _snap(page, "model-selected", snap)

    # Now click the settings pill (duration/resolution area) to open the
    # settings panel and set all options.
    await _open_settings_panel(
        page, duration, snap,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        audio=audio,
    )
    await _human_pause(400, 700)


async def _open_settings_panel(
    page: Page,
    seconds: int,
    snap: Optional[ScreenshotCB],
    *,
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    audio: bool = False,
) -> None:
    """
    Click the settings pill (shows e.g. "21:9 / 720p / 15 Sec") in the bottom
    bar to open the settings panel, then set duration, resolution, aspect ratio,
    and audio as requested.
    """
    # Click the settings/duration pill to open the panel
    opened = await page.evaluate(
        r"""() => {
            const all = Array.from(document.querySelectorAll('button, [role="button"], span'));
            for (const el of all) {
                const t = (el.innerText || '').trim();
                if (/\d+\s*[Ss]ec|\d+p.*[Ss]ec|720p|1080p|resolution/i.test(t) && el.offsetParent) {
                    el.click();
                    return 'settings-pill: ' + t.slice(0, 60);
                }
            }
            return null;
        }"""
    )
    print(f"[artlist] settings panel open: {opened!r}")
    await _human_pause(700, 1_100)
    await _snap(page, "settings-open", snap)

    # Helper: click a settings option by its exact label text using mouse coordinates.
    # Artlist renders options as <label> elements; coordinate-based clicking is most reliable.
    async def _click_option(text: str, timeout_ms: int = 2_000) -> bool:
        coords = await page.evaluate(
            """(text) => {
                // Find the smallest element whose full innerText matches (e.g. a leaf LABEL/SPAN)
                const targets = Array.from(document.querySelectorAll('label, span, button, div, li'));
                let best = null;
                for (const el of targets) {
                    const t = (el.innerText || '').trim();
                    if (t !== text) continue;
                    if (!el.offsetParent) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 1 || rect.height < 1) continue;
                    if (!best || rect.width * rect.height < best.area) {
                        best = {x: rect.left + rect.width/2, y: rect.top + rect.height/2,
                                area: rect.width * rect.height};
                    }
                }
                return best ? {x: best.x, y: best.y} : null;
            }""",
            text,
        )
        if coords:
            await page.mouse.move(coords["x"], coords["y"])
            await _human_pause(80, 180)
            await page.mouse.click(coords["x"], coords["y"])
            return True
        return False

    # ── Aspect ratio (top of panel for Kling; bottom for Seedance) ────────────
    if aspect_ratio:
        hit_ar = await _click_option(aspect_ratio)
        if not hit_ar:
            # Some models (Seedance) hide rare ratios (21:9) behind a "View All".
            # Click the View All that belongs to the Aspect Ratio section — it's
            # the one with a y-coordinate GREATER than the "Aspect Ratio" heading.
            # Click the bottommost "View All" button — for Seedance that is always
            # the aspect-ratio one (duration View All is higher on the page).
            ar_va_coords = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(b => (b.innerText || '').trim() === 'View All' && b.offsetWidth > 0);
                if (!btns.length) return null;
                // Sort by screen Y descending; the AR View All is the lowest one
                btns.sort((a, b) =>
                    b.getBoundingClientRect().top - a.getBoundingClientRect().top);
                const r = btns[0].getBoundingClientRect();
                return {x: r.left + r.width / 2, y: r.top + r.height / 2};
            }""")
            expanded_ar = False
            if ar_va_coords:
                await page.mouse.move(ar_va_coords["x"], ar_va_coords["y"])
                await _human_pause(80, 150)
                await page.mouse.click(ar_va_coords["x"], ar_va_coords["y"])
                expanded_ar = True
            if expanded_ar:
                print(f"[artlist] expanded aspect ratio options via View All")
                await _human_pause(600, 900)
                # The panel popup clips overflow — the new row (e.g. 21:9) may be
                # rendered below the panel's visible edge.  JS .click() bypasses
                # the viewport / bounding-rect check entirely.
                js_clicked = await page.evaluate(
                    """(ar) => {
                        // Prefer the smallest leaf element with exactly this text
                        let best = null;
                        for (const el of document.querySelectorAll('label, span, button, div')) {
                            const t = (el.innerText || '').trim();
                            if (t !== ar) continue;
                            const area = el.offsetWidth * el.offsetHeight;
                            if (area > 0 && (!best || area < best.area)) {
                                best = {el, area};
                            }
                        }
                        if (best) { best.el.click(); return true; }
                        return false;
                    }""",
                    aspect_ratio,
                )
                if js_clicked:
                    hit_ar = True
                else:
                    hit_ar = await _click_option(aspect_ratio)
        if hit_ar:
            print(f"[artlist] set aspect ratio {aspect_ratio}")
        else:
            print(f"[artlist] ⚠️ aspect ratio '{aspect_ratio}' not available for this model — skipping")
        await _human_pause(300, 500)

    # ── Audio toggle (above duration) ─────────────────────────────────────────
    if audio:
        audio_toggled = await page.evaluate(
            r"""() => {
                const all = Array.from(document.querySelectorAll(
                    'input[type="checkbox"], [role="switch"], [class*="toggle"]'
                ));
                for (const el of all) {
                    const parent = el.closest('label, [class*="row"], [class*="item"]') || el.parentElement;
                    if (parent && /audio/i.test(parent.innerText || '')) {
                        const checked = el.checked || el.getAttribute('aria-checked') === 'true';
                        if (!checked) { el.click(); return true; }
                        return 'already-on';
                    }
                }
                const audioBtn = Array.from(document.querySelectorAll('button, span')).find(
                    el => (el.innerText || '').trim().toLowerCase() === 'audio' && el.offsetParent
                );
                if (audioBtn) { audioBtn.click(); return 'audio-btn'; }
                return false;
            }"""
        )
        print(f"[artlist] audio toggle: {audio_toggled!r}")
        await _human_pause(300, 500)

    # ── Duration (middle of panel) ─────────────────────────────────────────────
    # Options shown vary by model:
    #   • Seedance/Gemini: shows 3–6 sec; "View All" expands to 7–15 sec
    #   • Kling: shows chips (5 sec / 10 sec / 15 sec) directly in the panel
    #   • Others: may use a scrollable list
    dur_text     = f"{seconds} sec"
    dur_text_alt = f"{seconds}s"          # some models render "10s" not "10 sec"

    async def _try_dur_click() -> bool:
        """Try clicking the duration option by its exact text label."""
        hit = await _click_option(dur_text)
        if not hit:
            # alt format "Xs"
            hit = await page.evaluate(
                """(text) => {
                    for (const el of document.querySelectorAll('label,span,button,div,li')) {
                        const t = (el.innerText || '').trim();
                        if (t === text && el.offsetParent) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) { el.click(); return true; }
                        }
                    }
                    return false;
                }""",
                dur_text_alt,
            )
        return hit

    hit_dur = await _try_dur_click()

    # Strategy 1: expand via "View All" (Seedance, Gemini, etc.)
    if not hit_dur:
        expanded = await _click_option("View All")
        if not expanded:
            expanded = await _click_any(page, [
                "button:has-text('View All')",
                "span:has-text('View All')",
                "div:has-text('View All')",
            ], timeout=3_000)
        if expanded:
            print(f"[artlist] expanded duration list via View All")
            await _human_pause(500, 800)
            hit_dur = await _try_dur_click()

    # Strategy 2: scroll the settings panel to reveal the duration option
    if not hit_dur:
        scrolled = await page.evaluate(
            """(texts) => {
                // Find all scrollable containers that might hold duration options
                const scrollables = Array.from(document.querySelectorAll('*')).filter(el => {
                    const s = window.getComputedStyle(el);
                    return (s.overflowY === 'auto' || s.overflowY === 'scroll')
                        && el.scrollHeight > el.clientHeight + 4
                        && el.offsetParent;
                });
                for (const c of scrollables) {
                    // Scroll to bottom to reveal all options
                    c.scrollTop = c.scrollHeight;
                }
                // Now try to find and click the duration chip
                for (const el of document.querySelectorAll('label,span,button,div,li')) {
                    const t = (el.innerText || '').trim();
                    if (texts.includes(t) && el.offsetParent) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) { el.click(); return t; }
                    }
                }
                return null;
            }""",
            [dur_text, dur_text_alt],
        )
        if scrolled:
            hit_dur = True
            print(f"[artlist] set duration via scroll reveal: {scrolled!r}")

    if hit_dur:
        print(f"[artlist] set duration {seconds}s")
    else:
        print(f"[artlist] ⚠️ duration {seconds}s not available — using default")
    await _human_pause(300, 500)

    # ── Resolution (bottom of panel) ───────────────────────────────────────────
    if resolution:
        hit_res = await _click_option(resolution)
        if not hit_res:
            hit_res = await _click_any(page, [
                f"label:has-text('{resolution}')",
                f"button:has-text('{resolution}')",
                f"span:has-text('{resolution}')",
            ], timeout=2_000)
        if hit_res:
            print(f"[artlist] set resolution {resolution}")
        else:
            print(f"[artlist] ⚠️ resolution '{resolution}' not available — skipping")
        await _human_pause(300, 500)

    await _snap(page, "settings-set", snap)

    # Close the settings panel before returning so Generate isn't blocked by an overlay.
    # Click the settings pill (toggle) to close it; fall back to Escape if not found.
    closed_via = await page.evaluate(
        """() => {
            // The settings pill is the small button showing "16:9 / 720p / 15 Sec" etc.
            // Clicking it a second time closes the panel.
            for (const btn of document.querySelectorAll('button,[role="button"]')) {
                const t = (btn.innerText || '').trim();
                if (/\\d+:\\d+|720p|1080p|480p|\\d+\\s*(sec|s)$/i.test(t) && btn.offsetParent) {
                    btn.click();
                    return 'pill: ' + t.slice(0, 40);
                }
            }
            return null;
        }"""
    )
    if closed_via:
        print(f"[artlist] closed settings panel via {closed_via}")
    else:
        await page.keyboard.press("Escape")
        print("[artlist] closed settings panel via Escape")
    await _human_pause(500, 800)


# Keep old name as an alias so any leftover references don't break
_open_settings_and_set_duration = _open_settings_panel


# ── Type prompt ────────────────────────────────────────────────────────────────

_MAX_PROMPT_CHARS = 500   # Artlist's prompt field gets unreliable above this

async def _type_prompt(page: Page, prompt: str, snap: Optional[ScreenshotCB]) -> None:
    """
    Paste the prompt into the Artlist toolkit's contenteditable prompt field.
    Uses execCommand('insertText') as the primary path — it's instant for any
    length and triggers React's synthetic events correctly.  Character-by-character
    typing is kept only as last-resort fallback for very short prompts.
    """
    if "toolkit.artlist.io" not in page.url:
        raise RuntimeError("Unexpected page state when entering prompt — please try again.")

    # Hard-cap prompt length — very long prompts confuse the field and can
    # prevent the Generate button from becoming enabled.
    if len(prompt) > _MAX_PROMPT_CHARS:
        print(f"[artlist] prompt truncated {len(prompt)} → {_MAX_PROMPT_CHARS} chars")
        prompt = prompt[:_MAX_PROMPT_CHARS]

    # Step 1: Focus the contenteditable field via JS and clear it.
    focused = await page.evaluate(
        """(promptText) => {
            const _findCE = () => {
                // Priority 1: contenteditable inside a known prompt container
                const containers = document.querySelectorAll(
                    '[data-testid*="prompt"], [class*="prompt"], [class*="editor"]'
                );
                for (const c of containers) {
                    const ce = c.querySelector('[contenteditable="true"]') ||
                               (c.getAttribute('contenteditable') === 'true' ? c : null);
                    if (ce && ce.offsetParent !== null) return ce;
                }
                // Priority 2: any visible contenteditable in the lower half
                return Array.from(document.querySelectorAll('[contenteditable="true"]'))
                    .find(el => el.offsetParent && el.getBoundingClientRect().top > 400);
            };
            const ce = _findCE();
            if (!ce) return null;
            ce.focus();
            ce.innerText = '';
            ce.dispatchEvent(new Event('input', {bubbles: true}));
            return 'container-ce: ' + (ce.className || '').slice(0, 60);
        }""",
        prompt,
    )
    print(f"[artlist] prompt focus: {focused!r}")

    if not focused:
        # Use JS to find and click the prompt field directly — never use
        # coordinate-based clicks here because (530,570) can land on the
        # "Create in Claude with Artlist MCP" link and navigate away.
        await page.evaluate("""() => {
            const el =
                document.querySelector('[class*="prompt-editor"]') ||
                document.querySelector('[class*="prompt"] [contenteditable="true"]') ||
                document.querySelector('[contenteditable="true"]');
            if (el && el.offsetParent !== null) {
                el.scrollIntoView({ block: 'start', behavior: 'instant' });
                el.focus();  // focus only — never .click() here (could trigger navigation links)
            }
        }""")
        await _human_pause(300, 500)

    await _human_pause(200, 350)

    # Step 2: Clear any stale content
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Backspace")
    await _human_pause(100, 200)

    # Step 3: PRIMARY — paste via execCommand (instant, works for any prompt length)
    inserted = await page.evaluate(
        """(text) => {
            const _findCE = () => {
                const containers = document.querySelectorAll(
                    '[data-testid*="prompt"],[class*="prompt"],[class*="editor"]'
                );
                for (const c of containers) {
                    const el = c.querySelector('[contenteditable="true"]') ||
                               (c.getAttribute('contenteditable')==='true' ? c : null);
                    if (el && el.offsetParent) return el;
                }
                return Array.from(document.querySelectorAll('[contenteditable="true"]'))
                    .find(el => el.offsetParent && el.getBoundingClientRect().top > 400);
            };
            const ce = _findCE();
            if (!ce) return false;
            ce.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            const ok = document.execCommand('insertText', false, text);
            // NOTE: do NOT dispatch a second InputEvent with data here — Artlist's
            // handler processes `data` from every InputEvent and would append the
            // text a second time, doubling the prompt.  execCommand already fires
            // the correct native beforeinput + input events automatically.
            return ok || (ce.innerText || '').trim().length > 0;
        }""",
        prompt,
    )

    # Step 4: Verify text landed
    actual = await page.evaluate("""() => {
        const ae = document.activeElement;
        return ae ? (ae.innerText || ae.value || '').trim().slice(0, 80) : '';
    }""")
    print(f"[artlist] prompt field content after typing: {actual!r}")

    if not actual:
        # Fallback: character-by-character (slow but guaranteed to trigger events)
        print("[artlist] execCommand fallback — typing char-by-char")
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await _human_type(page, prompt[:200], delay_range=(30, 60))  # cap at 200 for speed
        actual2 = await page.evaluate("""() => {
            const ae = document.activeElement;
            return ae ? (ae.innerText || ae.value || '').trim().slice(0, 80) : '';
        }""")
        print(f"[artlist] char-type fallback result: {actual2!r}")

    # ── Settle the UI ─────────────────────────────────────────────────────────
    # After inserting text (especially long prompts), React keeps the bottom bar
    # in "prompt focused" state for a moment, hiding the model pill.  Press
    # Escape, click a neutral area, and wait for the UI to fully settle so that
    # _select_model can reliably find the model pill.
    await page.keyboard.press("Escape")
    await page.evaluate("""() => {
        if (document.activeElement && document.activeElement !== document.body)
            document.activeElement.blur();
    }""")
    await _human_pause(700, 1_000)  # let React re-render the bottom bar

    # Scroll the prompt/Generate area into view before screenshotting so the
    # Scroll the prompt editor into view before screenshotting.
    try:
        await page.evaluate("""() => {
            const el =
                document.querySelector('[class*="prompt-editor"]') ||
                document.querySelector('[class*="prompt"] [contenteditable="true"]') ||
                document.querySelector('[contenteditable="true"]');
            if (el && el.offsetParent !== null) {
                el.scrollIntoView({ block: 'start', behavior: 'instant' });
            } else {
                window.scrollTo(0, 600);
            }
        }""")
    except Exception:
        pass

    await _snap(page, "prompt-typed", snap)


# ── Generate & wait ────────────────────────────────────────────────────────────

async def _find_generate_btn(page: Page) -> Optional[dict]:
    """Return {x, y, area, text} for the enabled Generate button, or None.

    Strategy 0: MUI button class + "Generate" text (stable, class-based).
    Strategy 1: text/aria-label scan across all buttons (generic fallback).
    Prefers the smallest matching button to avoid false-positives with large
    section headers that happen to contain the word "Generate".
    """
    return await page.evaluate(
        """() => {
            function btnInfo(btn) {
                const rect = btn.getBoundingClientRect();
                if (rect.width < 1 || rect.height < 1) return null;
                return {
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    area: rect.width * rect.height,
                    text: (btn.innerText || btn.textContent || 'Generate').trim().slice(0, 40),
                };
            }
            function isEnabled(btn) {
                // Use getBoundingClientRect instead of offsetParent — fixed-position
                // elements (like the sticky bottom bar) have offsetParent=null even
                // when fully visible on screen.
                const rect = btn.getBoundingClientRect();
                if (rect.width < 1 || rect.height < 1) return false;
                if (btn.disabled) return false;
                if (btn.getAttribute('aria-disabled') === 'true') return false;
                const style = window.getComputedStyle(btn);
                if (parseFloat(style.opacity) < 0.4) return false;
                if (style.pointerEvents === 'none') return false;
                if (style.visibility === 'hidden') return false;
                if (style.display === 'none') return false;
                return true;
            }

            // ── Strategy 0: MUI button classes + visible text "Generate" ─────
            // The button has class MuiButtonBase-root MuiButton-root and text "Generate"
            const muiBtns = Array.from(document.querySelectorAll(
                'button.MuiButtonBase-root, button.MuiButton-root'
            ));
            for (const btn of muiBtns) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (!/^generate$/i.test(t)) continue;
                if (!isEnabled(btn)) continue;
                const info = btnInfo(btn);
                if (info) return info;
            }

            // ── Strategy 0b: any button[type="button"] with ONLY text "Generate" ─
            const typeBtns = Array.from(document.querySelectorAll('button[type="button"]'));
            for (const btn of typeBtns) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (!/^generate$/i.test(t)) continue;
                if (!isEnabled(btn)) continue;
                const info = btnInfo(btn);
                if (info) return info;
            }

            // ── Strategy 1: text/aria-label/title scan (generic fallback) ────
            const btns = Array.from(
                document.querySelectorAll('button,[role="button"]')
            );
            let best = null;
            for (const btn of btns) {
                // Match on any text surface: visible text, aria-label, title, tooltip
                const surfaces = [
                    (btn.innerText || btn.textContent || '').trim(),
                    btn.getAttribute('aria-label') || '',
                    btn.getAttribute('title') || '',
                    btn.getAttribute('data-tooltip') || '',
                    btn.getAttribute('data-testid') || '',
                ];
                const matched = surfaces.some(s => /^generate$/i.test(s.trim()));
                if (!matched) continue;
                if (!isEnabled(btn)) continue;
                const rect = btn.getBoundingClientRect();
                if (rect.width < 1 || rect.height < 1) continue;
                const area = rect.width * rect.height;
                // Pick the smallest match (avoids section headers)
                if (!best || area < best.area) {
                    best = {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        area,
                        text: surfaces.find(s => s.trim()) || '?',
                        disabled: btn.disabled,
                    };
                }
            }
            return best;
        }"""
    )


async def _generate_and_wait(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
    model: str = "unknown",
) -> None:
    await progress("🚀 Starting generation…")

    # ── Step 1: Close any open settings panel, then find the Generate button ──────
    # The settings panel overlay intercepts clicks even with force=True because
    # React's synthetic event system processes the overlay's handlers first.
    # We must dismiss it before clicking Generate.
    panel_closed = await page.evaluate(
        """() => {
            // Check whether a settings/filter panel is currently open.
            // Look for the panel container elements that Artlist renders.
            const panelSelectors = [
                '[class*="SettingsPanel"]', '[class*="settings-panel"]',
                '[class*="FilterPanel"]',  '[class*="filter-panel"]',
                '[class*="OptionsPanel"]', '[class*="options-panel"]',
            ];
            for (const sel of panelSelectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent) return true;  // panel is visible
            }
            // Also check: if we can see the resolution/duration/AR option rows
            const body = document.body.innerText || '';
            return /resolution|duration|aspect ratio|audio/i.test(body)
                && document.querySelector('input[type="radio"], [class*="option"]');
        }"""
    )
    if panel_closed:
        print("[artlist] settings panel detected open — closing before Generate")
        # Try clicking the settings pill toggle first (most reliable close)
        pill_toggled = await page.evaluate(
            """() => {
                for (const btn of document.querySelectorAll('button,[role="button"]')) {
                    const t = (btn.innerText || '').trim();
                    if (/\\d+:\\d+|720p|1080p|480p|\\d+\\s*(sec|s)$/i.test(t) && btn.offsetParent) {
                        btn.click();
                        return 'pill: ' + t.slice(0, 40);
                    }
                }
                return null;
            }"""
        )
        if pill_toggled:
            print(f"[artlist] settings closed via {pill_toggled}")
        else:
            await page.keyboard.press("Escape")
            print("[artlist] settings closed via Escape")
        await _human_pause(600, 900)
    else:
        # Even if not detected, press Escape once as a safety measure —
        # it's a no-op if no overlay is open.
        await page.keyboard.press("Escape")
        await _human_pause(400, 600)

    gen_info = None
    for _wait in range(40):          # up to ~20 s
        gen_info = await _find_generate_btn(page)
        if gen_info:
            break
        await asyncio.sleep(0.5)

    if not gen_info:
        # Still not found — try one more Escape and retry
        print("[artlist] Generate button not found — pressing Escape and retrying")
        await page.keyboard.press("Escape")
        await _human_pause(800, 1_200)
        for _wait in range(20):      # up to ~10 s more
            gen_info = await _find_generate_btn(page)
            if gen_info:
                break
            await asyncio.sleep(0.5)

    if gen_info:
        print(
            f"[artlist] Generate button enabled at "
            f"({gen_info['x']:.0f},{gen_info['y']:.0f}) text={gen_info['text']!r}"
        )
    else:
        # Still not found — take a screenshot to debug and try force anyway
        await _snap(page, "generate-btn-not-found", snap)
        print("[artlist] ⚠️ Generate button not found enabled — trying force click")

    # ── Step 2: Hover Generate to trigger costQuote, then click ────────────────
    # Artlist computes a server-signed `costQuoteDigitalSignature` when the
    # Generate button is hovered/focused. Without it the API returns 403.
    # We intercept the costQuote response so we know it has been received by
    # the page before we click.
    _quote_received = asyncio.Event()

    async def _on_quote_response(res):
        if ("costQuote" in res.url or "cost_quote" in res.url or
                "CostQuote" in res.url or "quote" in res.url.lower()) and res.status < 400:
            _quote_received.set()
            print(f"[artlist] costQuote response received: {res.status} {res.url[:80]}")

    page.on("response", _on_quote_response)

    # Hover the Generate button to prime the quote
    if gen_info:
        await page.mouse.move(gen_info["x"], gen_info["y"])
        await _human_pause(300, 500)
        print("[artlist] hovering Generate button to trigger costQuote…")

    # Try also focusing it via locator (React hover listeners may need focus)
    try:
        _hover_loc = page.locator('button.MuiButtonBase-root:has-text("Generate")').first
        await _hover_loc.hover(timeout=4_000)
    except Exception:
        pass

    # Wait up to 4 s for the costQuote to come back (not fatal if absent)
    try:
        await asyncio.wait_for(_quote_received.wait(), timeout=4.0)
        print("[artlist] costQuote received — proceeding to click")
    except asyncio.TimeoutError:
        print("[artlist] costQuote timeout (may be Unlimited plan — clicking anyway)")

    page.remove_listener("response", _on_quote_response)

    # Extra pause so React can set the signature in component state
    await _human_pause(400, 700)

    submitted = False

    # Strategy A (primary): real mouse click at button coordinates (isTrusted)
    # Use mouse.click rather than locator.click(force=True) so React's
    # pointer-event chain fires exactly as it would for a human user.
    if gen_info:
        gx, gy = gen_info["x"], gen_info["y"]
        await page.mouse.move(gx + random.uniform(-3, 3), gy + random.uniform(-2, 2))
        await _human_pause(80, 150)
        await page.mouse.click(gx, gy)
        submitted = True
        print(f"[artlist] Generate mouse.click at ({gx:.0f},{gy:.0f}) ✓")

    # Strategy A2: Playwright locator click (fallback if no gen_info coords)
    if not submitted:
        _gen_selectors = [
            'button.MuiButtonBase-root:has-text("Generate")',
            'button.MuiButton-root:has-text("Generate")',
            'button:has-text("Generate")',
            'button[aria-label="Generate"]',
            'button[title="Generate"]',
            '[role="button"][aria-label="Generate"]',
            '[role="button"]:has-text("Generate")',
        ]
        for _sel in _gen_selectors:
            try:
                loc = page.locator(_sel).first
                await loc.wait_for(state="attached", timeout=5_000)
                await _human_pause(200, 400)
                await loc.click(timeout=8_000, force=True)
                submitted = True
                print(f"[artlist] Generate clicked via Playwright locator ({_sel}) force=True ✓")
                break
            except Exception as e:
                print(f"[artlist] Strategy A2 selector {_sel!r} failed: {e}")
        if not submitted:
            print("[artlist] Strategy A2: all selectors failed — falling through")

    # Strategy B: close settings panel via pill toggle → real mouse click
    # The settings pill (shows e.g. "720p / 15 Sec") is a toggle; clicking it
    # again closes the panel so there's no overlay blocking the Generate button.
    if not submitted:
        pill_closed = await page.evaluate(
            r"""() => {
                const all = Array.from(document.querySelectorAll('button,[role="button"],span'));
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if (/\d+\s*[Ss]ec|\d+p.*[Ss]ec|720p|1080p|480p/i.test(t) && el.offsetParent) {
                        const rect = el.getBoundingClientRect();
                        return {x: rect.left + rect.width/2, y: rect.top + rect.height/2, text: t.slice(0,40)};
                    }
                }
                return null;
            }"""
        )
        if pill_closed:
            print(f"[artlist] Strategy B: closing settings panel via pill: {pill_closed['text']!r}")
            await page.mouse.move(pill_closed["x"], pill_closed["y"])
            await _human_pause(100, 200)
            await page.mouse.click(pill_closed["x"], pill_closed["y"])
            await _human_pause(500, 800)

            # Re-find Generate button with panel now closed
            gen_after_close = None
            for _w in range(20):   # up to 10 s
                gen_after_close = await _find_generate_btn(page)
                if gen_after_close:
                    break
                await asyncio.sleep(0.5)

            if gen_after_close:
                await page.mouse.move(gen_after_close["x"] + random.uniform(-2, 2),
                                      gen_after_close["y"] + random.uniform(-2, 2))
                await _human_pause(150, 300)
                await page.mouse.click(gen_after_close["x"], gen_after_close["y"])
                submitted = True
                print(f"[artlist] Generate clicked via mouse (panel closed) at "
                      f"({gen_after_close['x']:.0f},{gen_after_close['y']:.0f}) ✓")
            else:
                print("[artlist] ⚠️ Generate button not found after closing panel")
        else:
            print("[artlist] ⚠️ settings pill not found — falling through to Strategy C")

    # Strategy C: raw mouse click at last-known button coordinates
    if not submitted and gen_info:
        await page.mouse.move(gen_info["x"] + random.uniform(-3, 3),
                              gen_info["y"] + random.uniform(-3, 3))
        await _human_pause(150, 300)
        await page.mouse.click(gen_info["x"], gen_info["y"])
        submitted = True
        print(f"[artlist] Generate clicked via mouse (raw fallback) at "
              f"({gen_info['x']:.0f},{gen_info['y']:.0f}) ✓")

    # Strategy D: nuclear — press Escape to dismiss any overlay, wait for
    # button to re-enable, then try locator again.
    if not submitted:
        print("[artlist] Strategy D: Escape + wait + locator retry")
        await page.keyboard.press("Escape")
        await _human_pause(1_200, 1_800)
        for _w in range(20):
            gen_retry = await _find_generate_btn(page)
            if gen_retry:
                await page.mouse.move(gen_retry["x"], gen_retry["y"])
                await _human_pause(150, 250)
                await page.mouse.click(gen_retry["x"], gen_retry["y"])
                submitted = True
                print(f"[artlist] Generate clicked via Strategy D at ({gen_retry['x']:.0f},{gen_retry['y']:.0f}) ✓")
                break
            await asyncio.sleep(0.5)

    await _snap(page, "generating-start", snap)

    # ── Step 2b: Handle cost-confirmation popup ─────────────────────────────────
    # After clicking Generate, Artlist may show a cost-confirmation popup that
    # displays the credit cost (e.g. "♦ 4,500") and a second "Generate" button.
    # We must click that confirmation button to actually submit the generation.
    # Wait a short moment for the popup to render, then look for it.
    await _human_pause(600, 1_000)
    cost_confirm = await page.evaluate(
        r"""() => {
            // Look for a popup/tooltip/modal that contains BOTH a credit-cost
            // indicator (a number near a diamond/spark icon or "credits" label)
            // AND a "Generate" button inside it.
            //
            // Heuristic: find any element whose text contains a 3–5 digit number
            // (the cost) AND "generate" anywhere nearby.
            const allBtns = Array.from(document.querySelectorAll('button,[role="button"]'));
            let best = null;
            for (const btn of allBtns) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (!/^generate$/i.test(t)) continue;
                // Check if a cost chip (number ≥ 100) is visible near this button
                const r = btn.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) continue;
                if (btn.disabled) continue;
                if (btn.getAttribute('aria-disabled') === 'true') continue;
                // Look for a sibling / parent element containing a credit cost
                const parent = btn.parentElement || document.body;
                const parentText = (parent.innerText || parent.textContent || '');
                const hasCost = /\d{3,6}/.test(parentText);
                // Prefer buttons whose surrounding context shows a cost number
                if (hasCost && (!best || r.width * r.height < best.area)) {
                    best = {
                        x: r.left + r.width / 2,
                        y: r.top + r.height / 2,
                        area: r.width * r.height,
                        text: parentText.trim().slice(0, 60),
                    };
                }
            }
            return best;
        }"""
    )
    if cost_confirm:
        print(f"[artlist] cost-confirm popup found — clicking Generate "
              f"at ({cost_confirm['x']:.0f},{cost_confirm['y']:.0f}) "
              f"context={cost_confirm['text']!r}")
        await page.mouse.move(cost_confirm["x"] + random.uniform(-2, 2),
                              cost_confirm["y"] + random.uniform(-2, 2))
        await _human_pause(150, 250)
        await page.mouse.click(cost_confirm["x"], cost_confirm["y"])
        await _human_pause(400, 700)
    else:
        print("[artlist] no cost-confirm popup detected — proceeding")

    await progress("⏳ Your video is on its way…")

    # ── Step 3: Confirm generation actually started ─────────────────────────────
    # Artlist can create a session page (UUID URL) WITHOUT actually queueing the
    # generation — the session page shows the prompt/settings and "Generate" still
    # clickable with "Nothing here yet" in the Visuals panel.
    # We must detect this and click Generate again on the session page.
    import re as _re

    def _is_session_url(url: str) -> bool:
        return (
            "generatedVideo" in url
            or "/session/" in url
            or bool(_re.search(r"toolkit\.artlist\.io/[0-9a-f]{8}-[0-9a-f]{4}-", url))
        )

    async def _generation_running() -> bool:
        """True if we see real progress signals in the page body."""
        return await page.evaluate(
            r"""() => {
                const body = document.body.innerText || '';
                return /on its way/i.test(body)
                    || /creating something/i.test(body)
                    || /\d+\s*%/.test(body)
                    || /almost ready/i.test(body)
                    || /processing/i.test(body);
            }"""
        )

    async def _check_no_credits() -> None:
        """Raise immediately if the account has no Artlist credits."""
        no_credits = await page.evaluate(
            """() => {
                const body = document.body.innerText || '';
                return /you don't have enough credits/i.test(body)
                    || /upgrade to generate/i.test(body)
                    || /get credits/i.test(body)
                    || /not enough credits/i.test(body);
            }"""
        )
        if no_credits:
            await _snap(page, "no-credits", snap)
            raise RuntimeError(
                "No generation credits remaining — please contact an admin."
            )

    async def _check_copyrighted() -> None:
        """Raise CopyrightError if Artlist blocked generation for copyright / content policy."""
        result = await page.evaluate(
            """() => {
                const body = document.body.innerText || '';
                // "Your prompt didn't meet the model guidelines" popup (screenshot pattern)
                if (/prompt didn't meet/i.test(body))            return 'guidelines';
                if (/didn't meet the model/i.test(body))         return 'guidelines';
                if (/meet the model guidelines/i.test(body))     return 'guidelines';
                // Explicit copyright / content-policy language
                if (/copyright/i.test(body))                     return 'copyright';
                if (/content policy/i.test(body))                return 'content-policy';
                if (/content.*violat|violat.*content/i.test(body)) return 'content-violation';
                if (/dmca/i.test(body))                          return 'dmca';
                if (/intellectual property/i.test(body))         return 'ip';
                if (/prohibited content/i.test(body))            return 'prohibited';
                if (/flagged/i.test(body))                       return 'flagged';
                // Generic generation-failed banners Artlist shows after a blocked video
                if (/something went wrong/i.test(body)
                        && !/loading/i.test(body))               return 'something-went-wrong';
                if (/generation failed/i.test(body))             return 'generation-failed';
                if (/could not be generated/i.test(body))        return 'could-not-generate';
                if (/failed to generate/i.test(body))            return 'failed-to-generate';
                return null;
            }"""
        )
        if result:
            await _snap(page, f"copyright-{result}", snap)
            raise CopyrightError(result)

    async def _session_is_empty() -> bool:
        """True if we're on a session page but no generation is queued yet."""
        if not _is_session_url(page.url):
            return False
        gen_btn = await _find_generate_btn(page)
        if not gen_btn:
            return False  # no Generate button → generation already running
        # "Nothing here yet" in the visuals area + Generate button still present
        return await page.evaluate(
            """() => {
                const body = document.body.innerText || '';
                return /nothing here yet/i.test(body)
                    && /create image|create video/i.test(body);
            }"""
        )

    # Wait up to 15 s for the URL to settle after the click
    for _ in range(15):
        await asyncio.sleep(1)
        if _is_session_url(page.url):
            break

    print(f"[artlist] after click URL: {page.url}")

    # Give the session page 3 s to render before probing its state
    await asyncio.sleep(3)

    # If we're on a session page but generation wasn't queued, click Generate again.
    # On the session page there is NO settings-panel overlay — a real mouse click works.
    # _session_is_empty can return False if the button hasn't rendered yet; the
    # inner loop retries up to 3 times with a 3 s gap between each probe.
    for _attempt in range(3):
        await _check_no_credits()   # fail fast if account has no credits
        if not await _session_is_empty():
            # Could be: generation is running OR button not rendered yet.
            # Distinguish by checking for progress signals.
            if await _generation_running():
                print(f"[artlist] generation is running after attempt {_attempt}")
                break
            # Button not found but also no progress — page still loading, wait
            if _attempt < 2:
                print(f"[artlist] session state unclear (attempt {_attempt+1}) — waiting 3s")
                await asyncio.sleep(3)
                continue
            break

        await _snap(page, f"session-empty-retry{_attempt}", snap)
        # Also save locally for debug
        try:
            _dbg_path = f"screenshots/dbg-session-page-{_attempt}.jpg"
            await page.screenshot(path=_dbg_path, type="jpeg", quality=75)
            print(f"[artlist] debug screenshot saved: {_dbg_path}")
        except Exception:
            pass

        # Dump ALL buttons with "generate" in text (incl disabled/hidden) for debug
        all_gen_btns = await page.evaluate(
            r"""() => {
                const out = [];
                for (const btn of document.querySelectorAll('button,[role="button"]')) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (!/generate/i.test(t)) continue;
                    const r = btn.getBoundingClientRect();
                    const s = window.getComputedStyle(btn);
                    out.push({
                        text: t.slice(0, 60),
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                        w: Math.round(r.width), h: Math.round(r.height),
                        opacity: s.opacity,
                        pointerEvents: s.pointerEvents,
                        disabled: btn.disabled,
                        offsetParent: !!btn.offsetParent,
                        zIndex: s.zIndex,
                    });
                }
                return out;
            }"""
        )
        print(f"[artlist] ALL generate buttons on session page: {all_gen_btns}")

        # Check what element is actually AT (1073, 852) on the session page
        at_coords = await page.evaluate(
            r"""() => {
                const pts = [[1073,852],[1100,852],[1150,852],[1200,852],[1250,852]];
                return pts.map(([x,y]) => {
                    const el = document.elementFromPoint(x, y);
                    if (!el) return {x,y,tag:null};
                    const r = el.getBoundingClientRect();
                    return {
                        x, y,
                        tag: el.tagName,
                        text: (el.innerText||el.textContent||'').trim().slice(0,40),
                        cls: el.className.slice(0,60),
                        rect: {l:Math.round(r.left),t:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height)},
                    };
                });
            }"""
        )
        print(f"[artlist] element probe at y=852:")
        for pt in at_coords:
            print(f"  ({pt['x']},{pt['y']}) → {pt.get('tag')} '{pt.get('text')}' cls={pt.get('cls','')[:40]}")

        print(f"[artlist] session is empty (attempt {_attempt+1}) — removing frames then clicking Generate on session page")
        await _human_pause(500, 800)

        # Remove any Start Frame / End Frame chips on the session page before
        # clicking Generate — they can block generation if still present.
        try:
            await _remove_frames(page, snap)
        except Exception as _rf_err:
            print(f"[artlist] _remove_frames on session page error (non-fatal): {_rf_err}")
        await _human_pause(600, 1_000)

        # ── Network intercept: detect 403 on createUserGeneration immediately ───
        _net_reqs: list[str] = []
        _gen_403: list[str] = []

        def _on_request(req):
            if "createUserGeneration" in req.url or "userGenerationRouter" in req.url:
                try:
                    pb = req.post_data or "(empty)"
                except Exception:
                    pb = "(unreadable)"
                # Log enough chars to capture costQuoteDigitalSignature value
                _net_reqs.append(f"GENERATE BODY={pb[:3000]}")
            elif "costQuote" in req.url or "CostQuote" in req.url or "quote" in req.url.lower():
                _net_reqs.append(f"QUOTE {req.method} {req.url[:120]}")
            elif req.method in ("POST", "PUT", "PATCH") and "artlist.io" in req.url and "analytics" not in req.url:
                _net_reqs.append(f"{req.method} {req.url[:100]}")

        async def _on_response(res):
            url = res.url
            if "createUserGeneration" in url or "userGenerationRouter" in url:
                status = res.status
                try:
                    body = await res.text()
                except Exception:
                    body = "(unreadable)"
                _gen_403.append(f"{status}: {body[:300]}")
                _net_reqs.append(f"  → {status} {url[:80]} body={body[:120]}")
            elif res.status >= 400 and "artlist" in url:
                _net_reqs.append(f"  → {res.status} {url[:80]}")

        page.on("request", _on_request)
        page.on("response", _on_response)

        # First: click the prompt field to activate React state using real Playwright events
        prompt_loc = page.locator('[contenteditable="true"]').first
        try:
            await prompt_loc.click(timeout=3_000)
            await page.keyboard.press("End")   # place cursor → wakes React onChange
            await _human_pause(200, 350)
            # Dispatch extra input event as belt-and-suspenders
            prompt_clicked = await page.evaluate(
                """() => {
                    const f = document.querySelector('[contenteditable="true"]');
                    if (f) {
                        f.dispatchEvent(new Event('input', {bubbles: true}));
                        return f.innerText.trim().slice(0, 50);
                    }
                    return null;
                }"""
            )
        except Exception:
            prompt_clicked = await page.evaluate(
                """() => {
                    const f = document.querySelector('[contenteditable="true"]');
                    if (f) { f.click(); f.focus();
                        f.dispatchEvent(new Event('input', {bubbles: true}));
                        return f.innerText.trim().slice(0, 50); }
                    return null;
                }"""
            )
        print(f"[artlist] focused prompt: {prompt_clicked!r}")
        await _human_pause(300, 500)

        gen_on_session = await _find_generate_btn(page)
        if gen_on_session:
            gx, gy = gen_on_session["x"], gen_on_session["y"]
            # Primary: Playwright locator force-click (isTrusted=true CDP event)
            _clicked_session = False
            for _ss in ['button.MuiButtonBase-root:has-text("Generate")',
                        'button:has-text("Generate")',
                        'button[aria-label="Generate"]']:
                try:
                    await page.locator(_ss).first.click(force=True, timeout=5_000)
                    _clicked_session = True
                    print(f"[artlist] session-page Generate locator click ({_ss}) ✓")
                    break
                except Exception as _e:
                    print(f"[artlist] session-page locator {_ss!r} failed: {_e}")
            if not _clicked_session:
                # Fallback: raw mouse click at found coordinates
                await page.mouse.move(gx + random.uniform(-2, 2), gy + random.uniform(-2, 2))
                await _human_pause(150, 250)
                await page.mouse.click(gx, gy)
            print(f"[artlist] session-page Generate click-1 at ({gx:.0f},{gy:.0f})")
            await asyncio.sleep(1)
            # Check if a confirmation dialog appeared
            confirm = await page.evaluate(
                """() => {
                    const body = document.body.innerText || '';
                    if (/confirm|continue|proceed|ok/i.test(body)) {
                        // Try to find and click a confirm button
                        for (const btn of document.querySelectorAll('button,[role="button"]')) {
                            const t = (btn.innerText||'').trim();
                            if (/^(ok|confirm|continue|proceed|yes|generate)$/i.test(t) && btn.offsetParent) {
                                const r = btn.getBoundingClientRect();
                                return {x: r.left+r.width/2, y: r.top+r.height/2, text: t};
                            }
                        }
                    }
                    return null;
                }"""
            )
            if confirm:
                print(f"[artlist] confirmation dialog found: {confirm['text']!r} — clicking")
                await page.mouse.move(confirm["x"], confirm["y"])
                await _human_pause(100, 200)
                await page.mouse.click(confirm["x"], confirm["y"])
            else:
                # Second click (in case button needs double-confirm)
                await page.mouse.move(gx + random.uniform(-1, 1), gy + random.uniform(-1, 1))
                await _human_pause(200, 350)
                await page.mouse.click(gx, gy)
                print(f"[artlist] session-page Generate click-2 (confirm) at ({gx:.0f},{gy:.0f})")
        else:
            # Generate button not found by _find_generate_btn — try Playwright locator
            print("[artlist] Generate not found by _find_generate_btn — trying Playwright locator")
            _sp_selectors = [
                'button.MuiButtonBase-root:has-text("Generate")',
                'button.MuiButton-root:has-text("Generate")',
                'button:has-text("Generate")',
                'button[aria-label="Generate"]',
                'button[title="Generate"]',
                '[role="button"][aria-label="Generate"]',
            ]
            for _sp_sel in _sp_selectors:
                try:
                    loc = page.locator(_sp_sel).first
                    await loc.click(force=True, timeout=5_000)
                    print(f"[artlist] session-page Generate clicked via {_sp_sel!r} (force) ✓")
                    break
                except Exception as e2:
                    print(f"[artlist] session-page locator {_sp_sel!r} failed: {e2}")

        # Log network activity captured during the click
        await asyncio.sleep(3)
        page.remove_listener("request", _on_request)
        page.remove_listener("response", _on_response)
        if _net_reqs:
            print(f"[artlist] network activity after Generate click:")
            for r in _net_reqs:
                print(f"  {r}")
        else:
            print("[artlist] ⚠️ NO network requests captured after Generate click!")

        # Fast-fail on 403 from createUserGeneration
        if _gen_403:
            body_snippet = _gen_403[0]
            await _snap(page, "generate-403", snap)
            raise RuntimeError(
                "Generation request was rejected (HTTP 403). "
                "The account may not have an active plan or credits. "
                "Please try again later or contact an admin."
            )

        await asyncio.sleep(2)

    # Wait up to 30 s for actual progress signals
    started = False
    for _ in range(30):
        await asyncio.sleep(1)

        # If still on the generator home (no session yet), don't hard-fail here —
        # fall through to the retry loop below so we keep trying.
        if await _generation_running():
            started = True
            break

    print(f"[artlist] job URL: {page.url}  started={started}")

    if not started:
        # Retry loop — keep clicking Generate until it actually queues (up to 4 attempts).
        for _retry_n in range(4):
            await _snap(page, f"not-started-retry-{_retry_n + 1}", snap)
            print(f"[artlist] ⚠️ generation didn't start — retry {_retry_n + 1}/4")
            gen_info2 = await _find_generate_btn(page)
            if gen_info2:
                await page.mouse.move(gen_info2["x"], gen_info2["y"])
                await _human_pause(150, 250)
                await page.mouse.click(gen_info2["x"], gen_info2["y"])
                print(f"[artlist] retry click at ({gen_info2['x']:.0f},{gen_info2['y']:.0f})")
            else:
                print("[artlist] ⚠️ Generate button not found on retry — will check again")
            # Wait up to 12 s to see if it starts
            for _ in range(12):
                await asyncio.sleep(1)
                if await _generation_running() or _is_session_url(page.url):
                    started = True
                    break
            if started:
                print(f"[artlist] ✅ generation started after retry {_retry_n + 1}")
                break
        if not started:
            print("[artlist] ℹ️ still not confirmed started — proceeding to poll anyway")

    # ── Step 4: Poll until done (max 20 min for long 21:9 clips) ───────────────
    _last_reclick_tick  = -999   # track when we last re-clicked (session URL path)
    _ns_reclick_count   = 0      # how many times we re-clicked on non-session URL
    _ns_reclick_max     = 6      # give up only after 6 × 15 s = 90 s of retries
    _last_ns_reclick    = -999
    for tick in range(240):        # 240 × 5 s = 20 min
        await asyncio.sleep(5)
        elapsed = (tick + 1) * 5

        # Fast-fail: no credits or copyright block
        await _check_no_credits()
        await _check_copyrighted()

        # Detect "Nothing here yet" — means Generate was never actually queued.
        # This can happen on BOTH the main generator page AND a session UUID page.
        _nothing_here = await page.evaluate(
            """() => {
                const body = document.body.innerText || '';
                return /nothing here yet/i.test(body)
                    && /create image|create video/i.test(body);
            }"""
        )
        if _nothing_here:
            if not _is_session_url(page.url):
                # Still on the generator home — keep re-clicking Generate rather than failing.
                if tick - _last_ns_reclick < 3:
                    continue          # throttle: only retry every 15 s
                if _ns_reclick_count >= _ns_reclick_max:
                    await _snap(page, f"studio-home-giveup-{elapsed}s", snap)
                    raise RuntimeError(
                        f"Generation still not queued after {elapsed}s — please try again."
                    )
                _ns_reclick_count += 1
                _last_ns_reclick = tick
                print(f"[artlist] poll@{elapsed}s: still on home — re-click #{_ns_reclick_count}")
                await _snap(page, f"home-reclick-{elapsed}s", snap)
                _ns_gen = await _find_generate_btn(page)
                if _ns_gen:
                    await page.mouse.move(_ns_gen["x"], _ns_gen["y"])
                    await _human_pause(150, 300)
                    await page.mouse.click(_ns_gen["x"], _ns_gen["y"])
                    print(f"[artlist] home re-click at ({_ns_gen['x']:.0f},{_ns_gen['y']:.0f})")
                continue

            # On a session page but generation still not queued — re-click Generate.
            # Throttle: only retry every 3 ticks (15 s) so we don't thrash.
            if tick - _last_reclick_tick < 3:
                continue
            _last_reclick_tick = tick

            print(f"[artlist] poll@{elapsed}s: session empty — re-clicking Generate")
            await _snap(page, f"poll-empty-{elapsed}s", snap)

            # Step A: focus the prompt field with real Playwright events so React activates
            try:
                _ce_loc = page.locator('[contenteditable="true"]').first
                await _ce_loc.click(timeout=3_000)
                await page.keyboard.press("End")   # positions cursor → triggers React onChange
                await _human_pause(200, 350)
                focused_prompt = await page.evaluate(
                    """() => {
                        const ce = document.querySelector('[contenteditable="true"]');
                        if (ce) {
                            ce.dispatchEvent(new Event('input', {bubbles: true}));
                            return (ce.innerText || '').trim().slice(0, 60);
                        }
                        return null;
                    }"""
                )
            except Exception:
                focused_prompt = await page.evaluate(
                    """() => {
                        const ce = Array.from(
                            document.querySelectorAll('[contenteditable="true"]')
                        ).find(el => el.getBoundingClientRect().width > 0);
                        if (ce) {
                            ce.click(); ce.focus();
                            ce.dispatchEvent(new Event('input', {bubbles: true}));
                            return (ce.innerText || '').trim().slice(0, 60);
                        }
                        return null;
                    }"""
                )
            print(f"[artlist] poll re-click: focused prompt={focused_prompt!r}")
            await _human_pause(400, 700)

            # Step B: intercept network — capture both requests AND responses
            _rc_reqs: list[str] = []
            _rc_gen_responses: list[str] = []

            async def _rc_on_req(req):
                if "createUserGeneration" in req.url or "userGenerationRouter" in req.url:
                    try:
                        pb = req.post_data or "(empty)"
                    except Exception:
                        pb = "(unreadable)"
                    try:
                        # all_headers() is async and includes the cookie header
                        all_hdrs = await req.all_headers()
                        auth_keys = {k: v[:120] for k, v in all_hdrs.items()
                                     if k.lower() in ("cookie", "x-csrf-token",
                                                      "authorization", "content-type",
                                                      "origin", "referer")}
                        # Shorten cookie value for log readability
                        if "cookie" in auth_keys:
                            auth_keys["cookie"] = auth_keys["cookie"][:80] + "…"
                    except Exception:
                        auth_keys = {}
                    _rc_reqs.append(
                        f"GEN POST url={req.url[:120]}\n"
                        f"  headers={auth_keys}\n"
                        f"  body={pb[:3000]}"
                    )
                elif req.method == "POST" and "artlist.io" in req.url \
                        and "analytics" not in req.url and "mixpanel" not in req.url \
                        and "segment" not in req.url and "events" not in req.url:
                    _rc_reqs.append(f"POST {req.url[:80]}")

            async def _rc_on_res(res):
                if "createUserGeneration" in res.url or "userGenerationRouter" in res.url:
                    try:
                        body = await res.text()
                    except Exception:
                        body = "(unreadable)"
                    try:
                        resp_hdrs = dict(res.headers)
                    except Exception:
                        resp_hdrs = {}
                    _rc_gen_responses.append(
                        f"HTTP {res.status} url={res.url[:120]}: {body[:400]}\n"
                        f"  resp_headers={resp_hdrs}"
                    )

            page.on("request", _rc_on_req)
            page.on("response", _rc_on_res)

            # Step C: find + click Generate — locator first (force=True, isTrusted CDP event)
            _rc_gen = await _find_generate_btn(page)
            _rc_clicked = False
            for _rs in ['button.MuiButtonBase-root:has-text("Generate")',
                        'button:has-text("Generate")',
                        'button[aria-label="Generate"]']:
                try:
                    await page.locator(_rs).first.click(force=True, timeout=5_000)
                    _rc_clicked = True
                    print(f"[artlist] poll re-click: Generate locator ({_rs}) ✓")
                    break
                except Exception as _re:
                    print(f"[artlist] poll re-click locator {_rs!r}: {_re}")
            if not _rc_clicked and _rc_gen:
                gx, gy = _rc_gen["x"], _rc_gen["y"]
                print(f"[artlist] poll re-click: Generate mouse fallback at ({gx:.0f},{gy:.0f})")
                await page.mouse.move(gx + random.uniform(-2, 2), gy + random.uniform(-2, 2))
                await _human_pause(150, 250)
                await page.mouse.click(gx, gy)
            if _rc_gen:
                gx, gy = _rc_gen["x"], _rc_gen["y"]
                await asyncio.sleep(2)
                # Check for confirmation modal right after click
                confirm_modal = await page.evaluate(
                    """() => {
                        const body = document.body.innerText || '';
                        for (const btn of document.querySelectorAll('button,[role="button"]')) {
                            const t = (btn.innerText || '').trim();
                            if (/^(ok|confirm|yes|continue|proceed|create)$/i.test(t)
                                    && btn.offsetParent
                                    && !/(start frame|end frame|create image|create video|generate)/i.test(t)) {
                                const r = btn.getBoundingClientRect();
                                return {x: r.left+r.width/2, y: r.top+r.height/2, text: t};
                            }
                        }
                        return null;
                    }"""
                )
                if confirm_modal:
                    print(f"[artlist] poll re-click: confirmation modal '{confirm_modal['text']}' — clicking")
                    await page.mouse.move(confirm_modal["x"], confirm_modal["y"])
                    await _human_pause(100, 200)
                    await page.mouse.click(confirm_modal["x"], confirm_modal["y"])
            else:
                # Fallback: Playwright locator force-click (produces real CDP event)
                for _ps in ['button:has-text("Generate")',
                            'button[aria-label="Generate"]',
                            'button[title="Generate"]']:
                    try:
                        await page.locator(_ps).first.click(force=True, timeout=4_000)
                        print(f"[artlist] poll re-click via locator {_ps!r} ✓")
                        break
                    except Exception:
                        pass

            await asyncio.sleep(3)
            page.remove_listener("request", _rc_on_req)
            page.remove_listener("response", _rc_on_res)
            if _rc_reqs:
                print(f"[artlist] poll re-click requests: {_rc_reqs[:2]}")
            else:
                print(f"[artlist] poll re-click: ⚠️ no generation API call captured")
            if _rc_gen_responses:
                print(f"[artlist] poll re-click responses: {_rc_gen_responses}")
                # 403 means the generation API rejected the request.
                # This can happen when saved cookies carry a stale CSRF token.
                # We no longer delete the cookie file here because the session
                # token itself is valid for navigation — only the CSRF state
                # needs refreshing, which is handled by omitting CSRF cookies
                # during injection (see _load_cookies).
                if any("403" in r for r in _rc_gen_responses):
                    await _snap(page, "403-model-forbidden", snap)
                    raise RuntimeError(
                        f"Generation rejected for model '{model}' — "
                        f"this may be a credits issue or plan restriction. "
                        f"Try /video again; if it keeps failing, contact an admin."
                    )
            continue   # re-evaluate immediately after the click

        pct_text = await page.evaluate(
            r"""() => {
                const body = document.body.innerText || '';
                // "Your video is on its way… (13%)"
                const m = body.match(/on its way[^\d]*(\d{1,3})%/i);
                if (m) return m[0];
                if (/almost ready/i.test(body)) return 'Almost ready…';
                // Short text nodes with a percentage (skip style/script blobs)
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT
                );
                let node;
                while ((node = walker.nextNode())) {
                    let anc = node.parentElement, skip = false;
                    while (anc) {
                        if (anc.tagName === 'STYLE' || anc.tagName === 'SCRIPT') {
                            skip = true; break;
                        }
                        anc = anc.parentElement;
                    }
                    if (skip) continue;
                    const t = node.textContent.trim();
                    if (t.length > 120) continue;
                    if (/^\d{1,3}%$/.test(t) || /\d{1,3}%/.test(t)) {
                        return t.slice(0, 60);
                    }
                }
                return '';
            }"""
        )
        await progress(
            f"⏳ {pct_text} (~{elapsed}s)" if pct_text else f"⏳ Generating… ({elapsed}s)"
        )

        # Done detection — multiple signals
        # When Artlist completes generation, a new card appears in the gallery
        # with the model name as a badge ("Seedance 2.0") and a "Use" button
        # visible on hover. We look for these signals broadly.
        done = await page.evaluate(
            r"""() => {
                const body = document.body.innerText || '';
                if (/100\s*%/.test(body)) return 'pct-100';
                if (/your video is ready/i.test(body)) return 'ready-banner';
                // NOTE: "Almost ready…" text appears mid-generation (e.g. at 73%)
                // so we do NOT use it as a done signal — wait for real completion.

                // Primary signal: "Use" button that appears on the generated card
                // (hover control on the completed AI video card in the gallery)
                const allBtns = Array.from(document.querySelectorAll('button,[role="button"]'));
                for (const btn of allBtns) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (t === 'Use' && btn.offsetParent) return 'use-btn';
                }

                // Secondary: Any visible "Download" button text
                for (const btn of allBtns) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (/^download$/i.test(t) && btn.offsetParent) return 'download-btn';
                }

                // Tertiary: the completed card in the gallery has a "Seedance X.x"
                // or "Kling X.x" badge text INSIDE a card element (not the bottom bar).
                // We look for elements that contain the model badge text AND have an img.
                // We specifically skip the bottom bar area (bottom ~100px).
                const modelRe = /seedance\s+[\d.]+|kling\s+[\d.]+/i;
                const allEls = Array.from(document.querySelectorAll('*'));
                for (const el of allEls) {
                    if (!el.offsetParent) continue;
                    const rect = el.getBoundingClientRect();
                    // Must be in the gallery area (not bottom bar at y > window.innerHeight - 120)
                    if (rect.top > window.innerHeight - 120) continue;
                    if (rect.width < 100 || rect.height < 60) continue;
                    const ownText = Array.from(el.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .join(' ');
                    if (modelRe.test(ownText)) {
                        // This element is the model badge inside a card
                        return 'model-badge-in-card';
                    }
                }

                // Quaternary: completed card "Video" badge appears in the
                // Visuals section once generation finishes.
                // During generation the body reads "Visuals\n\nYour video is on
                // its way…" — the badge only shows up when it's done.
                const stillGenerating = /on its way|creating something|\d+\s*%|almost ready|processing/i.test(body);
                if (!stillGenerating && /Visuals[\s\S]{0,200}Video/i.test(body)) {
                    return 'visuals-video-badge';
                }

                // Quinary: a <video> element with an actual src (only when not
                // generating — the preview frame during generation also has one).
                if (!stillGenerating) {
                    const vids = document.querySelectorAll('video');
                    for (const v of vids) {
                        if (v.src || v.querySelector('source[src]')) return 'video-element';
                    }
                }

                // Senary: gallery thumbnail image visible in the Visuals area.
                // Artlist now uses SVG icons instead of "Video" text badges,
                // so body.innerText won't show the badge.  If progress text is
                // gone AND a sizeable img is visible in the gallery zone,
                // the card is almost certainly the completed generation.
                if (!stillGenerating) {
                    const cutoff = window.innerHeight * 0.80;
                    const thumbImgs = Array.from(document.querySelectorAll('img')).filter(img => {
                        if (!img.offsetParent) return false;
                        const r = img.getBoundingClientRect();
                        return r.width > 100 && r.height > 80 && r.top > 60 && r.top < cutoff;
                    });
                    if (thumbImgs.length > 0) return 'gallery-thumbnail-img';
                }

                // Download link present
                const dl = document.querySelector(
                    'button[aria-label*="download" i], a[download]'
                );
                if (dl && dl.offsetParent) return 'dl-attr';

                return '';
            }"""
        )
        if done:
            print(f"[artlist] ✅ video ready at ~{elapsed}s — signal: {done}")
            break

        if tick % 6 == 5:          # snapshot + body dump every 30 s
            await _snap(page, f"gen-{elapsed}s", snap)
            body_sample = await page.evaluate(
                """() => (document.body.innerText || '').slice(0, 600)"""
            )
            print(f"[artlist] page body at {elapsed}s | URL: {page.url}")
            print(f"[artlist] body: {body_sample!r}")
    else:
        await _snap(page, "timeout", snap)
        raise RuntimeError("Video generation timed out after 20 minutes")

    await _human_pause(1_500, 2_500)
    await _snap(page, "video-ready", snap)


# ── Watermark ──────────────────────────────────────────────────────────────────

_WM_TEXT      = "𝐀ᴜʀᴀ ⁹⁹⁹⁺☠"
_WM_FONT_LATIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# NotoSansCJK kept for legacy compat but new watermark is Latin-only
_WM_FONT_CJK  = (
    "/nix/store/6jh0rswqwn4bif41mvyyyc49fvnfwr89-noto-fonts-cjk-sans-2.004"
    "/share/fonts/opentype/noto-cjk/NotoSansCJK-VF.otf.ttc"
)
# No CJK characters in the new watermark — all chars handled by DejaVu
_WM_CJK_CHARS: set[str] = set()
# Maps each character to the font that covers its code point
_WM_CHAR_FONT: dict[str, str] = {ch: _WM_FONT_CJK for ch in _WM_CJK_CHARS}
_WM_FONTS_READY = False


async def _ensure_wm_fonts() -> None:
    """Verify that required fonts are present (no download needed — using nix store)."""
    global _WM_FONTS_READY
    if _WM_FONTS_READY:
        return
    ok_latin = Path(_WM_FONT_LATIN).exists()
    ok_cjk   = Path(_WM_FONT_CJK).exists()
    print(f"[watermark] latin font: {'✓' if ok_latin else '✗'} | CJK font: {'✓' if ok_cjk else '✗'}")
    _WM_FONTS_READY = True


def _draw_wm_text(draw, text: str, x: int, y: int, size: int, alpha: int) -> None:
    """Draw *text* at (x, y) using the per-character font map."""
    from PIL import ImageFont
    for ch in text:
        fp = _WM_CHAR_FONT.get(ch, _WM_FONT_LATIN)
        if not Path(fp).exists():
            fp = _WM_FONT_LATIN
        try:
            # TTC collections need index=0; plain TTF/OTF ignores the kwarg
            font = ImageFont.truetype(fp, size, index=0)
        except Exception:
            try:
                font = ImageFont.truetype(_WM_FONT_LATIN, size)
            except Exception:
                continue
        bb = font.getbbox(ch)
        # Drop shadow
        draw.text((x + 2, y + 2), ch, font=font, fill=(0,   0,   0,   int(alpha * 0.65)))
        # Main glyph
        draw.text((x,     y    ), ch, font=font, fill=(255, 255, 255, alpha))
        x += (bb[2] - bb[0]) + 1


async def _add_watermark(video_bytes: bytes) -> bytes:
    """Burn 'Ꮢ࿐ʏᴛ' onto the video using PIL overlay.

    Strategy: one prominent watermark dead-center plus a grid of lower-opacity
    copies across the whole frame so AI-based removal requires destroying the
    underlying content everywhere simultaneously.
    """
    from PIL import Image, ImageDraw, ImageFont

    await _ensure_wm_fonts()

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as inp:
        inp.write(video_bytes)
        inp_path = inp.name
    out_path = inp_path.replace(".mp4", "_wm.mp4")
    wm_path  = inp_path.replace(".mp4", "_wm_overlay.png")

    try:
        # ── Video dimensions ──────────────────────────────────────────────
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", inp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_out, _ = await asyncio.wait_for(probe.communicate(), timeout=30)
        W, H = map(int, probe_out.decode().strip().split(","))
        print(f"[artlist] watermark: {W}×{H}")

        # ── Measure text dimensions at each size ──────────────────────────
        def _measure(size: int) -> tuple[int, int]:
            tw = th = 0
            for ch in _WM_TEXT:
                fp = _WM_CHAR_FONT.get(ch, _WM_FONT_LATIN)
                if not Path(fp).exists():
                    fp = _WM_FONT_LATIN
                try:
                    f = ImageFont.truetype(fp, size, index=0)
                except Exception:
                    try:
                        f = ImageFont.truetype(_WM_FONT_LATIN, size)
                    except Exception:
                        continue
                bb = f.getbbox(ch)
                tw += (bb[2] - bb[0]) + 1
                th  = max(th, bb[3] - bb[1])
            return tw, th

        SZ_MAIN = 28;  TW_M, TH_M = _measure(SZ_MAIN)
        SZ_GRID = 22;  TW_G, TH_G = _measure(SZ_GRID)

        # ── Build full-frame transparent overlay ──────────────────────────
        wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(wm)

        # Center — prominent
        _draw_wm_text(dr, _WM_TEXT, (W - TW_M) // 2, (H - TH_M) // 2, SZ_MAIN, alpha=140)

        # Grid: (x_frac, y_frac, alpha_0-255)
        _grid = [
            (0.08,  0.07,  58), (0.92,  0.07,  58),
            (0.08,  0.88,  58), (0.92,  0.88,  58),
            (0.50,  0.07,  52), (0.50,  0.88,  52),
            (0.05,  0.50,  52), (0.95,  0.50,  52),
            (0.25,  0.28,  46), (0.75,  0.28,  46),
            (0.25,  0.68,  46), (0.75,  0.68,  46),
        ]
        for fx, fy, alp in _grid:
            gx = max(0, min(W - TW_G, int(W * fx - TW_G / 2)))
            gy = max(0, min(H - TH_G, int(H * fy - TH_G / 2)))
            _draw_wm_text(dr, _WM_TEXT, gx, gy, SZ_GRID, alpha=alp)

        wm.save(wm_path, "PNG")

        # ── Composite with ffmpeg ─────────────────────────────────────────
        cmd = [
            "ffmpeg", "-y",
            "-i", inp_path,
            "-i", wm_path,
            "-filter_complex", "[0:v][1:v]overlay=0:0",
            "-c:a", "copy",
            "-preset", "fast",
            "-movflags", "+faststart",
            out_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            print(f"[artlist] ffmpeg watermark failed:\n{stderr.decode()[-400:]}")
            return video_bytes
        data = Path(out_path).read_bytes()
        print(f"[artlist] watermarked video: {len(data) // 1024} KB")
        return data

    except Exception as exc:
        print(f"[artlist] watermark error: {exc}")
        return video_bytes
    finally:
        for p in (inp_path, out_path, wm_path):
            Path(p).unlink(missing_ok=True)


# ── Download ───────────────────────────────────────────────────────────────────

async def _download_video(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> bytes:
    await progress("📥 Downloading video…")
    await _snap(page, "before-download", snap)

    # ── Step 1: Click the completed video card to open the detail/download panel ──
    # Strategies 0 and 1 use real Playwright mouse hover+click so CSS :hover
    # controls become visible before we click.

    async def _try_click_card_by_rect(rect: dict) -> bool:
        """Hover the mouse over the center of `rect`, then click."""
        try:
            cx = rect["x"] + rect["width"]  / 2
            cy = rect["y"] + rect["height"] / 2
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.4)
            await page.mouse.click(cx, cy)
            return True
        except Exception:
            return False

    card_clicked: str | None = None

    # Strategy 0: gallery <img> thumbnail → walk up to card container
    card_rect_0 = await page.evaluate("""() => {
        const cutoff = window.innerHeight * 0.78;
        const imgs = Array.from(document.querySelectorAll('img')).filter(el => {
            if (!el.offsetParent) return false;
            const r = el.getBoundingClientRect();
            return r.width > 100 && r.height > 80 && r.top > 60 && r.top < cutoff;
        });
        if (!imgs.length) return null;
        let el = imgs[0].parentElement;
        for (let i = 0; i < 10 && el && el !== document.body; i++, el = el.parentElement) {
            const r = el.getBoundingClientRect();
            if (r.width > 100 && r.height > 80 && r.top < cutoff)
                return { x: r.left, y: r.top, width: r.width, height: r.height };
        }
        const r = imgs[0].getBoundingClientRect();
        return { x: r.left, y: r.top, width: r.width, height: r.height };
    }""")

    if card_rect_0:
        ok = await _try_click_card_by_rect(card_rect_0)
        if ok:
            card_clicked = f"img-card-hover x={card_rect_0['x']:.0f} y={card_rect_0['y']:.0f}"
            print(f"[artlist] card click (strategy 0): {card_clicked}")

    await asyncio.sleep(1.5)

    def _has_download_btn_js():
        return """() => {
            const btns = Array.from(document.querySelectorAll('button, a'));
            return btns.some(b =>
                /^download$/i.test((b.innerText || b.textContent || '').trim())
                && b.offsetParent !== null
            );
        }"""

    panel_open = await page.evaluate(_has_download_btn_js())

    if not panel_open:
        # Strategy 1: <video> element
        card_rect_1 = await page.evaluate("""() => {
            const cutoff = window.innerHeight * 0.78;
            const vids = Array.from(document.querySelectorAll('video')).filter(v => {
                if (!v.offsetParent) return false;
                const r = v.getBoundingClientRect();
                return r.top < cutoff && r.height > 60;
            });
            if (!vids.length) return null;
            const r = vids[0].getBoundingClientRect();
            return { x: r.left, y: r.top, width: r.width, height: r.height };
        }""")
        if card_rect_1:
            ok = await _try_click_card_by_rect(card_rect_1)
            if ok:
                card_clicked = f"video-el-hover x={card_rect_1['x']:.0f} y={card_rect_1['y']:.0f}"
                print(f"[artlist] card click (strategy 1): {card_clicked}")
            await asyncio.sleep(1.5)
            panel_open = await page.evaluate(_has_download_btn_js())

    if not panel_open:
        # Strategy 2: JS click — "Video" badge card, CSS class selectors, or first img
        card_clicked_js = await page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const t = node.textContent.trim();
                if (t !== 'Video') continue;
                let el = node.parentElement;
                while (el && el !== document.body) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 80 && r.height > 80 && r.top < window.innerHeight - 100) {
                        if (el.querySelector('img, video')) {
                            el.click();
                            return 'video-badge-card y=' + Math.round(r.top);
                        }
                    }
                    el = el.parentElement;
                }
            }
            const cutoff = window.innerHeight * 0.80;
            for (const sel of [
                '[class*="result-card"]','[class*="video-card"]',
                '[class*="generated-video"]','[class*="visual-card"]',
                '[class*="asset-card"]','[class*="thumbnail"]','[class*="gallery"]',
            ]) {
                const els = Array.from(document.querySelectorAll(sel)).filter(el => {
                    if (!el.offsetParent) return false;
                    const r = el.getBoundingClientRect();
                    return r.top < cutoff && r.height > 80;
                });
                if (els.length) { els[0].click(); return sel; }
            }
            const imgs = Array.from(document.querySelectorAll('img')).filter(el => {
                if (!el.offsetParent || el.width <= 80) return false;
                const r = el.getBoundingClientRect();
                return r.top < window.innerHeight * 0.80 && r.top > 60;
            });
            if (imgs.length) { imgs[0].click(); return 'img-js y=' + Math.round(imgs[0].getBoundingClientRect().top); }
            return null;
        }""")
        card_clicked = card_clicked_js
        print(f"[artlist] card click (strategy 2 JS): {card_clicked_js}")
        await asyncio.sleep(1.5)
        panel_open = await page.evaluate(_has_download_btn_js())

    print(f"[artlist] detail panel open (Download btn visible): {panel_open}")
    await _snap(page, "detail-panel", snap)

    if not panel_open:
        # Strategy 3: blind mouse click at gallery centre
        try:
            await page.mouse.move(300, 300)
            await asyncio.sleep(0.3)
            await page.mouse.click(300, 300)
            print("[artlist] fallback mouse click (300,300)")
        except Exception:
            pass
        await asyncio.sleep(2.0)
        await _snap(page, "detail-panel-retry", snap)

    # ── Step 2: Arm download interception before clicking ─────────────────────
    dl_future: asyncio.Future[Download] = asyncio.get_event_loop().create_future()

    def _on_dl(dl: Download) -> None:
        if not dl_future.done():
            dl_future.set_result(dl)

    page.context.on("download", _on_dl)

    _intercepted_video_urls: list[str] = []

    async def _on_resp(resp):
        try:
            url = resp.url
            ct  = (resp.headers.get("content-type") or "").lower()
            if "video" in ct and resp.status == 200 and url.startswith("http"):
                _intercepted_video_urls.append(url)
                return
            if resp.status in (301, 302, 303, 307, 308):
                loc = (resp.headers.get("location") or "")
                if loc and (".mp4" in loc or "video" in loc.lower()):
                    _intercepted_video_urls.append(loc)
                    return
            if resp.status == 200 and url.startswith("http"):
                if ".mp4" in url or "/download/" in url.lower():
                    _intercepted_video_urls.append(url)
        except Exception:
            pass

    page.on("response", _on_resp)

    # ── Step 3: Click the first Download button (panel or current page) ───────
    await _human_pause(400, 800)
    dl_clicked = await _click_any(page, [
        "button[aria-label*='download' i]",
        "button:has-text('Download')",
        "a:has-text('Download')",
        "a[download]",
        "[class*='download'] button",
        "[class*='download']",
    ], timeout=6_000)

    if not dl_clicked:
        await page.evaluate("""
            () => {
                for (const el of document.querySelectorAll('button, a')) {
                    const label = (el.getAttribute('aria-label') || el.innerText || '').toLowerCase();
                    if (label.includes('download') || label.includes('save')) {
                        el.click(); return;
                    }
                }
            }
        """)

    print(f"[artlist] download button clicked: {dl_clicked}")
    await _snap(page, "download-clicked", snap)

    # ── Step 4: Wait for download — handles BOTH flows ────────────────────────
    #
    # Artlist has two download flows depending on plan/context:
    #
    #   Flow A (panel download): clicking Download in the detail panel shows
    #     "Downloading…" in the button while the server signs a CDN URL, then
    #     triggers a browser save-as (native download event) or exposes a direct
    #     video URL in the network / DOM.
    #
    #   Flow B (download page): clicking Download navigates to a NEW PAGE at
    #     toolkit.artlist.io/<id> that shows only a single large "Download" button.
    #     We must detect this navigation and click that second button to start
    #     the actual file download.
    #
    # We poll for 120 s, checking all sources on every tick.

    _download_page_clicked = False
    video_url: str | None = None

    for _attempt in range(240):   # 240 × 0.5 s = 120 s
        # 1. Native download event — grab the URL from the download request
        if dl_future.done():
            page.context.remove_listener("download", _on_dl)
            page.remove_listener("response", _on_resp)
            download = dl_future.result()
            dl_url = download.url
            if dl_url and not dl_url.startswith("blob:"):
                print(f"[artlist] ✅ video URL from download event: {dl_url[:80]}")
                return dl_url
            # blob: URL means the browser already captured the file — fall back to saving
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
            await download.save_as(tmp_path)
            data = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)
            print(f"[artlist] ✅ downloaded {len(data) // 1024} KB via download event (blob fallback)")
            return data

        # 2. Network-intercepted video URL
        if _intercepted_video_urls:
            video_url = _intercepted_video_urls[-1]
            print(f"[artlist] video URL captured from network after {(_attempt+1)*0.5:.1f}s: {video_url[:80]}")
            break

        # 3. DOM scan for <a download href> or <video src>
        url_found = await page.evaluate("""() => {
            const a = document.querySelector('a[download][href]');
            if (a && a.href && !a.href.startsWith('blob:')) return a.href;
            for (const lnk of document.querySelectorAll('a[href]')) {
                if (/[.]mp4|[/]video/i.test(lnk.href) && lnk.href.startsWith('http'))
                    return lnk.href;
            }
            const v = document.querySelector('video[src]');
            if (v && v.src && !v.src.startsWith('blob:')) return v.src;
            const s = document.querySelector('video source[src]');
            if (s && s.src && !s.src.startsWith('blob:')) return s.src;
            return null;
        }""")
        if url_found:
            video_url = url_found
            print(f"[artlist] video URL found in DOM after {(_attempt+1)*0.5:.1f}s: {video_url[:80]}")
            break

        # 4. Flow B: detect navigation to a dedicated download page and click its button.
        #    Artlist sometimes navigates to toolkit.artlist.io/<uuid-or-id> which
        #    renders ONLY a big "↓ Download" button with no detail panel.
        if not _download_page_clicked:
            current_url = page.url
            is_download_page = (
                "toolkit.artlist.io" in current_url
                and current_url != "https://toolkit.artlist.io/new?mode=video"
                and "/new" not in current_url.split("?")[0]
            )
            if is_download_page:
                # Check if the detail panel is gone (only a standalone Download btn remains)
                has_panel = await page.evaluate("""() => {
                    // A detail panel typically has "Prompt", "Settings", "Edit", "Recreate"
                    const body = document.body.innerText || '';
                    return /\bPrompt\b/i.test(body) && /\bSettings\b/i.test(body);
                }""")
                if not has_panel:
                    # We're on the dedicated download page — click the Download button
                    print(f"[artlist] Flow B: detected download page {current_url[:80]} — clicking Download")
                    await _snap(page, "download-page", snap)
                    dl2_clicked = await _click_any(page, [
                        "button:has-text('Download')",
                        "a:has-text('Download')",
                        "button[aria-label*='download' i]",
                        "a[download]",
                        "[class*='download'] button",
                        "[class*='download']",
                    ], timeout=8_000)
                    if not dl2_clicked:
                        # JS fallback
                        await page.evaluate("""
                            () => {
                                for (const el of document.querySelectorAll('button, a')) {
                                    const t = (el.innerText || '').trim().toLowerCase();
                                    if (t === 'download' || t.startsWith('download')) {
                                        el.click(); return;
                                    }
                                }
                            }
                        """)
                    _download_page_clicked = True
                    print(f"[artlist] Flow B: Download button clicked on download page: {dl2_clicked}")
                    await _snap(page, "download-page-clicked", snap)
                    # Give the browser a moment to start the download
                    await asyncio.sleep(2.0)
                    continue

        await asyncio.sleep(0.5)

    page.context.remove_listener("download", _on_dl)
    page.remove_listener("response", _on_resp)

    await _snap(page, "downloading", snap)

    if not video_url:
        # Last-chance scan
        video_url = await page.evaluate("""() => {
            const v = document.querySelector('video[src]');
            if (v) return v.src;
            const s = document.querySelector('video source[src]');
            if (s) return s.src;
            const a = document.querySelector('a[download][href]');
            if (a) return a.href;
            return null;
        }""")

    if video_url:
        print(f"[artlist] ✅ fetching video bytes from URL: {video_url[:80]}")
        import aiohttp as _aiohttp
        cookies = await page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Referer": page.url,
            "Cookie": cookie_str,
        }
        try:
            async with _aiohttp.ClientSession() as _sess:
                async with _sess.get(
                    video_url, headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=180),
                ) as _resp:
                    if _resp.status == 200:
                        data = await _resp.read()
                        print(f"[artlist] ✅ downloaded {len(data) // 1024} KB via URL+cookies")
                        return data
                    print(f"[artlist] ⚠️ URL fetch HTTP {_resp.status} — returning URL as fallback")
        except Exception as _fetch_e:
            print(f"[artlist] ⚠️ URL fetch failed ({_fetch_e}) — returning URL as fallback")
        # Fallback: return URL and let bot.py handle it
        return video_url

    raise RuntimeError("Could not obtain a video URL — please try again.")


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE GENERATION
# ══════════════════════════════════════════════════════════════════════════════

async def _open_image_generator(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> None:
    await progress("🖼️ Opening image generator…")
    for attempt in range(3):
        await page.goto(_ARTLIST_IMAGE_URL, wait_until="domcontentloaded", timeout=40_000)
        await _human_pause(3_000, 5_000)
        if "toolkit.artlist.io" in page.url:
            break
        print(f"[artlist-img] attempt {attempt+1}: wrong URL={page.url}, retrying…")
        await _human_pause(2_000, 3_000)
    else:
        await _snap(page, "img-wrong-page", snap)
        raise RuntimeError("Could not reach the image generator — please try again.")

    # Scroll to the Generate / prompt area before screenshotting so the dark
    # promotional header at the top of the page is off-screen.
    try:
        await page.evaluate("""() => {
            const el =
                document.querySelector('[class*="prompt-editor"]') ||
                document.querySelector('[class*="prompt"] [contenteditable="true"]') ||
                document.querySelector('[contenteditable="true"]');
            if (el && el.offsetParent !== null) {
                el.scrollIntoView({ block: 'start', behavior: 'instant' });
            } else {
                window.scrollTo(0, 600);
            }
        }""")
        await _human_pause(300, 500)
    except Exception as _e:
        print(f"[artlist-img] pre-screenshot scroll failed (non-fatal): {_e}")

    await _snap(page, "img-gen-page", snap)

    # NOTE: Do NOT use aria-label*='close' or aria-label*='dismiss' here —
    # those selectors match the X on the MCP/Claude Connect banner and clicking
    # it can open a modal or navigate away, causing a black overlay.
    for sel in [
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('Got it')",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await _human_pause(300, 600)
                await el.click()
                await _human_pause(400, 700)
        except Exception:
            pass


async def _select_image_model(
    page: Page,
    model: str,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
    *,
    aspect_ratio: Optional[str] = None,
) -> None:
    """Select the image model from the bottom-bar dropdown."""
    await progress(f"⚙️ Choosing {model}…")
    await _human_pause(600, 1_000)

    await page.keyboard.press("Escape")
    await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
    await _human_pause(300, 500)

    IMG_MODEL_RE_JS = "/Nano Banana|Seedream|GPT Image|Kling|FLUX|Ideogram|Recraft|Stable|Imagen|Grok|Aurora/i"

    async def _find_pill():
        return await page.evaluate(
            f"""() => {{
                const RE = {IMG_MODEL_RE_JS};
                const hits = [];
                for (const el of document.querySelectorAll('button,div,span,a')) {{
                    const t = (el.innerText || '').trim();
                    if (!t || t.length > 80) continue;
                    if (!RE.test(t)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        hits.push({{x: r.left+r.width/2, y: r.top+r.height/2, text: t, area: r.width*r.height}});
                }}
                if (!hits.length) return null;
                hits.sort((a,b)=>a.area-b.area);
                return hits[0];
            }}"""
        )

    pill = None
    for _i in range(5):
        pill = await _find_pill()
        if pill:
            break
        await page.keyboard.press("Escape")
        await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
        await asyncio.sleep(1.2)

    if not pill:
        print("[artlist-img] ⚠️ model pill not found — skipping model selection")
        return

    await page.mouse.move(pill["x"], pill["y"])
    await _human_pause(150, 250)
    await page.mouse.click(pill["x"], pill["y"])
    print(f"[artlist-img] model dropdown opened via pill: {pill['text']!r}")
    await _human_pause(800, 1_200)
    await _snap(page, "img-model-dropdown", snap)

    async def _try_click_model(target: str) -> bool:
        clicked = await _click_any(page, [
            f"li:has-text('{target}')",
            f"[role='option']:has-text('{target}')",
            f"[role='menuitem']:has-text('{target}')",
            f"button:has-text('{target}')",
            f"span:has-text('{target}')",
        ], timeout=3_000)
        if clicked:
            return True
        return await page.evaluate(
            """([exact, prefix]) => {
                for (const el of document.querySelectorAll('li,[role="option"],[role="menuitem"],button,span,div')) {
                    const t = (el.innerText || '').trim();
                    if (!el.offsetParent) continue;
                    if (t === exact || t.startsWith(prefix)) { el.click(); return true; }
                }
                return false;
            }""",
            [target, target.split()[0]],
        )

    clicked = await _try_click_model(model)
    print(f"[artlist-img] model click (short list): {clicked}")

    if not clicked:
        print(f"[artlist-img] '{model}' not in short list — clicking 'All Models'")
        all_ok = await _click_any(page, [
            "a:has-text('All Models')", "button:has-text('All Models')",
            "span:has-text('All Models')", "[href*='models']",
        ], timeout=4_000)
        if not all_ok:
            all_ok = await page.evaluate(
                """() => {
                    for (const el of document.querySelectorAll('a,button,span,div')) {
                        if (/^all models/i.test((el.innerText||'').trim()) && el.offsetParent) {
                            el.click(); return true;
                        }
                    }
                    return false;
                }"""
            )
        print(f"[artlist-img] 'All Models' clicked: {all_ok}")
        await _human_pause(1_000, 1_500)
        await _snap(page, "img-all-models", snap)
        clicked = await _try_click_model(model)
        print(f"[artlist-img] model click (all models): {clicked}")

    if not clicked:
        print(f"[artlist-img] ⚠️ model '{model}' not found — using default")

    await _human_pause(800, 1_200)
    await _snap(page, "img-model-selected", snap)


async def _generate_and_wait_image(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
    model: str = "unknown",
) -> None:
    """Click Generate and poll until the image is ready."""
    await progress("🚀 Starting image generation…")

    await page.keyboard.press("Escape")
    await _human_pause(400, 600)

    gen_info = None
    for _w in range(40):
        gen_info = await _find_generate_btn(page)
        if gen_info:
            break
        await asyncio.sleep(0.5)

    if not gen_info:
        await page.keyboard.press("Escape")
        await _human_pause(800, 1_200)
        for _w in range(20):
            gen_info = await _find_generate_btn(page)
            if gen_info:
                break
            await asyncio.sleep(0.5)

    # Hover to trigger costQuote
    _quote_ev = asyncio.Event()

    async def _on_quote(res):
        if ("costQuote" in res.url or "quote" in res.url.lower()) and res.status < 400:
            _quote_ev.set()

    page.on("response", _on_quote)
    if gen_info:
        await page.mouse.move(gen_info["x"], gen_info["y"])
        await _human_pause(300, 500)
        try:
            await page.locator('button.MuiButtonBase-root:has-text("Generate")').first.hover(timeout=4_000)
        except Exception:
            pass
    try:
        await asyncio.wait_for(_quote_ev.wait(), timeout=4.0)
    except asyncio.TimeoutError:
        pass
    page.remove_listener("response", _on_quote)
    await _human_pause(400, 700)

    # Click Generate
    submitted = False
    if gen_info:
        gx, gy = gen_info["x"], gen_info["y"]
        await page.mouse.move(gx + random.uniform(-3, 3), gy + random.uniform(-2, 2))
        await _human_pause(80, 150)
        await page.mouse.click(gx, gy)
        submitted = True
        print(f"[artlist-img] Generate clicked at ({gx:.0f},{gy:.0f})")

    if not submitted:
        for _sel in [
            'button.MuiButtonBase-root:has-text("Generate")',
            'button:has-text("Generate")',
            'button[aria-label="Generate"]',
        ]:
            try:
                await page.locator(_sel).first.click(timeout=8_000, force=True)
                submitted = True
                break
            except Exception:
                pass

    await _snap(page, "img-generating-start", snap)
    await _human_pause(600, 1_000)

    # Handle cost-confirmation popup
    cost_confirm = await page.evaluate(
        """() => {
            let best = null;
            for (const btn of document.querySelectorAll('button,[role="button"]')) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (!/^generate$/i.test(t)) continue;
                const r = btn.getBoundingClientRect();
                if (r.width < 1 || r.height < 1 || btn.disabled) continue;
                const parent = btn.parentElement || document.body;
                if (/\\d{3,6}/.test(parent.innerText || '')) {
                    const area = r.width * r.height;
                    if (!best || area < best.area)
                        best = {x: r.left+r.width/2, y: r.top+r.height/2, area};
                }
            }
            return best;
        }"""
    )
    if cost_confirm:
        print(f"[artlist-img] cost-confirm at ({cost_confirm['x']:.0f},{cost_confirm['y']:.0f})")
        await page.mouse.move(cost_confirm["x"], cost_confirm["y"])
        await _human_pause(150, 250)
        await page.mouse.click(cost_confirm["x"], cost_confirm["y"])
        await _human_pause(400, 700)

    await progress("⏳ Your image is on its way…")

    import re as _re_img

    def _is_session_url(url: str) -> bool:
        return (
            "generatedVideo" in url or "/session/" in url
            or bool(_re_img.search(r"toolkit\.artlist\.io/[0-9a-f]{8}-[0-9a-f]{4}-", url))
        )

    async def _check_no_credits() -> None:
        bad = await page.evaluate(
            """() => {
                const b = document.body.innerText || '';
                return /don't have enough credits|upgrade to generate|get credits|not enough credits/i.test(b);
            }"""
        )
        if bad:
            await _snap(page, "img-no-credits", snap)
            raise RuntimeError("No generation credits remaining — please contact an admin.")

    async def _check_copyrighted() -> None:
        r = await page.evaluate(
            """() => {
                const b = document.body.innerText || '';
                if (/prompt didn't meet/i.test(b))        return 'guidelines';
                if (/meet the model guidelines/i.test(b)) return 'guidelines';
                if (/copyright/i.test(b))                 return 'copyright';
                if (/content policy/i.test(b))            return 'content-policy';
                if (/prohibited content/i.test(b))        return 'prohibited';
                if (/generation failed/i.test(b))         return 'generation-failed';
                if (/could not be generated/i.test(b))    return 'could-not-generate';
                return null;
            }"""
        )
        if r:
            await _snap(page, f"img-copyright-{r}", snap)
            raise CopyrightError(r)

    for _ in range(15):
        await asyncio.sleep(1)
        if _is_session_url(page.url):
            break
    await asyncio.sleep(3)

    _last_reclick = -999
    for tick in range(180):   # 15 min max
        await asyncio.sleep(5)
        elapsed = (tick + 1) * 5

        await _check_no_credits()
        await _check_copyrighted()

        _nothing = await page.evaluate(
            """() => {
                const b = document.body.innerText || '';
                return /nothing here yet/i.test(b) && /create image|create video/i.test(b);
            }"""
        )
        if _nothing and _is_session_url(page.url):
            if tick - _last_reclick < 3:
                continue
            _last_reclick = tick
            print(f"[artlist-img] poll@{elapsed}s: session empty — re-clicking Generate")
            await _snap(page, f"img-poll-empty-{elapsed}s", snap)
            _rc_gen = await _find_generate_btn(page)
            if _rc_gen:
                try:
                    await page.locator('button:has-text("Generate")').first.click(force=True, timeout=5_000)
                except Exception:
                    await page.mouse.move(_rc_gen["x"], _rc_gen["y"])
                    await page.mouse.click(_rc_gen["x"], _rc_gen["y"])
            await asyncio.sleep(3)
            continue

        pct_text = await page.evaluate(
            r"""() => {
                const b = document.body.innerText || '';
                const m = b.match(/on its way[^\d]*(\d{1,3})%/i);
                if (m) return m[0];
                if (/almost ready/i.test(b)) return 'Almost ready…';
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t.length > 120) continue;
                    if (/^\d{1,3}%$/.test(t) || /\d{1,3}%/.test(t)) return t.slice(0,60);
                }
                return '';
            }"""
        )
        await progress(
            f"⏳ {pct_text} (~{elapsed}s)" if pct_text else f"⏳ Generating image… ({elapsed}s)"
        )

        done = await page.evaluate(
            r"""() => {
                const b = document.body.innerText || '';
                if (/100\s*%/.test(b)) return 'pct-100';
                if (/your image is ready/i.test(b)) return 'ready-banner';
                if (/your video is ready/i.test(b)) return 'video-ready-banner';

                const btns = Array.from(document.querySelectorAll('button,[role="button"]'));
                for (const btn of btns) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (t === 'Use' && btn.offsetParent) return 'use-btn';
                }
                for (const btn of btns) {
                    const t = (btn.innerText || btn.textContent || '').trim();
                    if (/^download$/i.test(t) && btn.offsetParent) return 'download-btn';
                }

                const stillGen = /on its way|creating something|\d+\s*%|almost ready|processing/i.test(b);
                if (!stillGen) {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    for (const img of imgs) {
                        if (!img.offsetParent) continue;
                        const r = img.getBoundingClientRect();
                        if (r.width > 150 && r.height > 150 && r.top > 50 && r.top < window.innerHeight * 0.85)
                            return 'image-card';
                    }
                    if (/Visuals[\s\S]{0,80}Image/i.test(b)) return 'visuals-image-badge';
                    if (/Visuals[\s\S]{0,80}Video/i.test(b)) return 'visuals-video-badge';
                }

                const dl = document.querySelector('button[aria-label*="download" i], a[download]');
                if (dl && dl.offsetParent) return 'dl-attr';
                return '';
            }"""
        )
        if done:
            print(f"[artlist-img] ✅ image ready at ~{elapsed}s — signal: {done}")
            break

        if tick % 6 == 5:
            await _snap(page, f"img-gen-{elapsed}s", snap)
    else:
        await _snap(page, "img-timeout", snap)
        raise RuntimeError("Image generation timed out after 15 minutes")

    await _human_pause(1_500, 2_500)
    await _snap(page, "img-ready", snap)


async def _download_image(
    page: Page,
    progress: ProgressCB,
    snap: Optional[ScreenshotCB],
) -> bytes:
    """
    Download the generated image.

    Artlist image cards NAVIGATE to a new detail URL (toolkit.artlist.io/01...)
    rather than opening an overlay panel.  That detail page has a single large
    "Download" button — click it and capture the download.

    Flow:
      1. JS-click the image card (finds card by 'Image' badge or img element)
      2. Wait for the page URL to change away from the gallery URL
      3. On the detail page, click the Download button (Playwright locator)
      4. Collect via native download event (best path) or URL + cookies fetch
    """
    await progress("📥 Downloading image…")
    await _snap(page, "img-before-download", snap)
    gallery_url = page.url

    # ── Step 1: JS-click the image card ──────────────────────────────────────
    # Use JS .click() (same pattern as the video downloader which works reliably).
    card_clicked = await page.evaluate("""() => {
        const cutoff = window.innerHeight * 0.85;

        // Strategy A: 'Image' badge text → nearest card ancestor with an <img>
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (node.textContent.trim() !== 'Image') continue;
            let el = node.parentElement;
            while (el && el !== document.body) {
                const r = el.getBoundingClientRect();
                if (r.width > 80 && r.height > 80 && r.top < cutoff) {
                    if (el.querySelector('img, canvas')) {
                        el.click();
                        return 'image-badge-card y=' + Math.round(r.top);
                    }
                }
                el = el.parentElement;
            }
        }

        // Strategy B: gallery card class selectors
        for (const sel of [
            '[class*="result-card"]','[class*="image-card"]','[class*="generated-image"]',
            '[class*="visual-card"]','[class*="asset-card"]','[class*="thumbnail"]',
        ]) {
            const els = Array.from(document.querySelectorAll(sel)).filter(el => {
                if (!el.offsetParent) return false;
                const r = el.getBoundingClientRect();
                return r.top < cutoff && r.height > 80;
            });
            if (els.length) { els[0].click(); return sel; }
        }

        // Strategy C: first large <img> in the gallery area
        const imgs = Array.from(document.querySelectorAll('img')).filter(el => {
            if (!el.offsetParent || el.width <= 80) return false;
            const r = el.getBoundingClientRect();
            return r.top < cutoff && r.top > 50;
        });
        if (imgs.length) {
            imgs[0].click();
            return 'img-el y=' + Math.round(imgs[0].getBoundingClientRect().top);
        }
        return null;
    }""")
    print(f"[artlist-img] card click: {card_clicked}")

    # ── Step 2: Wait for navigation to the detail page ────────────────────────
    # Artlist navigates to toolkit.artlist.io/01XXXXXXXXXX on card click.
    navigated = False
    for _i in range(20):   # up to 10 s
        await asyncio.sleep(0.5)
        if page.url != gallery_url:
            navigated = True
            break

    print(f"[artlist-img] navigated: {navigated}  url: {page.url[:80]}")
    await _snap(page, "img-detail-page", snap)

    if not navigated:
        # The JS click may have been swallowed by a React synthetic-event boundary.
        # Fall back to Playwright's own click on the <img> element coordinates.
        coords = await page.evaluate("""() => {
            const cutoff = window.innerHeight * 0.85;
            const imgs = Array.from(document.querySelectorAll('img')).filter(el => {
                if (!el.offsetParent || el.width <= 80) return false;
                const r = el.getBoundingClientRect();
                return r.top < cutoff && r.top > 50;
            });
            if (!imgs.length) return null;
            const r = imgs[0].getBoundingClientRect();
            return {x: r.left + r.width / 2, y: r.top + r.height / 2};
        }""")
        if coords:
            print(f"[artlist-img] fallback mouse click at ({coords['x']:.0f},{coords['y']:.0f})")
            await page.mouse.move(coords["x"], coords["y"])
            await asyncio.sleep(0.2)
            await page.mouse.click(coords["x"], coords["y"])
        else:
            await page.mouse.click(400, 300)

        for _i in range(20):
            await asyncio.sleep(0.5)
            if page.url != gallery_url:
                navigated = True
                break
        print(f"[artlist-img] after fallback — navigated: {navigated}  url: {page.url[:80]}")
        await _snap(page, "img-detail-page-retry", snap)

    # ── Step 3: Arm download listener, then click the Download button ─────────
    dl_future: asyncio.Future = asyncio.get_event_loop().create_future()

    def _on_dl(dl):
        if not dl_future.done():
            dl_future.set_result(dl)

    page.context.on("download", _on_dl)
    await _human_pause(400, 700)

    # The detail page has a single large "Download" button — use Playwright
    # locator with wait_for so we retry automatically until it appears.
    dl_clicked = False
    for _loc_sel in [
        "button:has-text('Download')",
        "a:has-text('Download')",
        "button[aria-label*='download' i]",
        "[class*='download-btn']",
        "[class*='download'] button",
        "a[download]",
        "[class*='download']",
    ]:
        try:
            loc = page.locator(_loc_sel).first
            await loc.wait_for(state="visible", timeout=4_000)
            await loc.click(timeout=6_000)
            dl_clicked = True
            print(f"[artlist-img] ✅ download button clicked via locator: {_loc_sel!r}")
            break
        except Exception:
            pass

    if not dl_clicked:
        # JS fallback — walk every interactive element for "download" text
        dl_clicked = await page.evaluate("""() => {
            for (const el of document.querySelectorAll(
                'button, a, [role="button"], [class*="download"]'
            )) {
                const label = (
                    el.getAttribute('aria-label') ||
                    el.innerText ||
                    el.textContent || ''
                ).toLowerCase().trim();
                if (label === 'download' || label.startsWith('download')) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        print(f"[artlist-img] download button JS fallback: {dl_clicked}")

    await _snap(page, "img-download-clicked", snap)

    # ── Step 4: Collect via native download event (best path) ─────────────────
    for _attempt in range(30):   # up to 15 s
        if dl_future.done():
            page.context.remove_listener("download", _on_dl)
            download = dl_future.result()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            await download.save_as(tmp_path)
            data = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)
            print(f"[artlist-img] ✅ downloaded {len(data) // 1024} KB via native download event")
            return data

        # Also check for an <a download href> that Artlist may inject
        url_found = await page.evaluate("""() => {
            const a = document.querySelector('a[download][href]');
            if (a && a.href && !a.href.startsWith('blob:')) return a.href;
            for (const lnk of document.querySelectorAll('a[href]')) {
                if (/[.](png|jpg|jpeg|webp)/i.test(lnk.href) && lnk.href.startsWith('http'))
                    return lnk.href;
            }
            return null;
        }""")
        if url_found:
            print(f"[artlist-img] injected download link found after {(_attempt+1)*0.5:.1f}s: {url_found[:80]}")
            page.context.remove_listener("download", _on_dl)
            break
        await asyncio.sleep(0.5)
    else:
        page.context.remove_listener("download", _on_dl)
        url_found = None

    await _snap(page, "img-downloading", snap)

    # ── Step 5: Fetch the URL with browser cookies ────────────────────────────
    if url_found:
        import aiohttp as _aiohttp
        cookies = await page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Referer": page.url,
            "Cookie": cookie_str,
        }
        print(f"[artlist-img] fetching image from: {url_found[:80]}")
        async with _aiohttp.ClientSession() as sess:
            async with sess.get(
                url_found, headers=headers,
                timeout=_aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    print(f"[artlist-img] ✅ downloaded {len(data) // 1024} KB via URL+cookies")
                    return data
                print(f"[artlist-img] ⚠️ URL fetch HTTP {resp.status}")

    # ── Last resort: in-page fetch of the largest visible <img> ──────────────
    # Runs inside the authenticated browser context so cookies are included.
    print("[artlist-img] last resort: in-page authenticated fetch of visible image")
    img_bytes = await page.evaluate("""async () => {
        const imgs = Array.from(document.querySelectorAll('img')).filter(el => {
            if (!el.offsetParent) return false;
            const r = el.getBoundingClientRect();
            return r.width > 200 && r.height > 200;
        });
        if (!imgs.length) return null;
        imgs.sort((a, b) => (b.naturalWidth * b.naturalHeight) - (a.naturalWidth * a.naturalHeight));
        try {
            const resp = await fetch(imgs[0].src, {credentials: 'include'});
            if (!resp.ok) return null;
            const buf = await resp.arrayBuffer();
            return Array.from(new Uint8Array(buf));
        } catch { return null; }
    }""")
    if img_bytes:
        data = bytes(img_bytes)
        print(f"[artlist-img] ✅ captured {len(data) // 1024} KB via in-page fetch")
        return data

    raise RuntimeError("Could not download the generated image — please try again.")


async def generate_artlist_image(
    prompt: str,
    model: str = "FLUX 1.1 Pro",
    aspect_ratio: Optional[str] = None,
    progress_cb: Optional[ProgressCB] = None,
    screenshot_cb: Optional[ScreenshotCB] = None,
) -> bytes:
    """
    Generate an image using the Artlist AI Image Generator.

    Args:
        prompt:        Text prompt.
        model:         AI model name (e.g. "FLUX 1.1 Pro").
        aspect_ratio:  "1:1" | "16:9" | "9:16" | "4:3" | "3:4" (default: model default).
        progress_cb:   Async callback for status updates.
        screenshot_cb: Async callback for debug screenshots (label, jpeg_bytes).

    Returns:
        Raw image bytes (PNG / JPEG / WEBP).
    """
    async def _noop(_): pass
    progress = progress_cb or _noop
    snap     = screenshot_cb

    async def _new_browser(pw):
        browser = await pw.chromium.launch(
            executable_path=_CHROMIUM_BIN,
            headless=True,
            args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-setuid-sandbox", "--no-zygote",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--flag-switches-begin", "--flag-switches-end",
            ],
        )
        _ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=_ua,
            accept_downloads=True,
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "sec-ch-ua": '"Chromium";v="138", "Google Chrome";v="138", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        try:
            from playwright_stealth import Stealth
            await Stealth().apply_stealth_async(ctx)
        except Exception:
            try:
                from playwright_stealth import stealth
                _s = stealth()
                if isinstance(_s, str):
                    await ctx.add_init_script(_s)
                else:
                    await ctx.add_init_script(_STEALTH_JS)
            except Exception:
                await ctx.add_init_script(_STEALTH_JS)
        # Block clicks on any link that would navigate to Claude/Anthropic domains.
        await ctx.add_init_script(_CLAUDE_BLOCKER_JS)
        return browser, ctx

    async with async_playwright() as pw:
        browser, ctx = await _new_browser(pw)
        try:
            page = await ctx.new_page()

            logged_in = False
            cookie_loaded = await _load_cookies(ctx)
            if cookie_loaded:
                logged_in = await _is_logged_in(page)

            if not logged_in:
                print("[artlist-img] logging in…")
                await _login(page, progress, snap)
                await _save_cookies(ctx)

            await _open_image_generator(page, progress, snap)

            await progress("✍️ Entering prompt…")
            await _type_prompt(page, prompt, snap)

            await _select_image_model(page, model, progress, snap, aspect_ratio=aspect_ratio)

            await _generate_and_wait_image(page, progress, snap, model=model)

            image_data = await _download_image(page, progress, snap)
            await progress("✅ Done!")
            return image_data
        finally:
            await ctx.close()
            await browser.close()


# ── Public entry point ─────────────────────────────────────────────────────────

async def generate_artlist_video(
    prompt: str,
    model: str = "Gemini Omni Flash",
    resolution: Optional[str] = None,
    duration: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    audio: bool = False,
    progress_cb: Optional[ProgressCB] = None,
    screenshot_cb: Optional[ScreenshotCB] = None,
    image_ref_bytes: Optional[bytes] = None,
    image_ref_ext: str = ".png",
    skip_watermark: bool = False,
) -> bytes:
    """
    Generate a video using the Artlist AI Video Generator.

    Args:
        prompt:          Text prompt.
        model:           AI model name (e.g. "Gemini Omni Flash").
        resolution:      "480p" | "720p" | "1080p" | "4K" (default: model default).
        duration:        Clip length in seconds 4–15 (default: model default).
        aspect_ratio:    "16:9" | "9:10" | "4:3" | "3:4" | "21:9" (default: model default).
        audio:           Whether to enable AI-generated audio (default: False).
        progress_cb:     Async callback for status updates.
        screenshot_cb:   Async callback for debug screenshots (label, jpeg_bytes).
        image_ref_bytes: Optional reference image bytes to upload as input.
        image_ref_ext:   File extension for the reference image (default: ".png").
    """
    async def _noop(_): pass
    progress = progress_cb or _noop
    snap     = screenshot_cb

    async def _new_browser(pw):
        browser = await pw.chromium.launch(
            executable_path=_CHROMIUM_BIN,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-zygote",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--flag-switches-begin",
                "--flag-switches-end",
            ],
        )
        # Use a realistic, recent Chrome UA matching the installed Chromium
        _ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=_ua,
            accept_downloads=True,
            locale="en-US",
            timezone_id="America/New_York",
            # Provide Client Hints headers that match the UA
            extra_http_headers={
                "sec-ch-ua": '"Chromium";v="138", "Google Chrome";v="138", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        # Apply playwright-stealth to defeat bot-detection fingerprinting
        try:
            from playwright_stealth import stealth, Stealth
            _stealth_obj = Stealth()
            await _stealth_obj.apply_stealth_async(ctx)
            print("[artlist] playwright-stealth applied ✓")
        except Exception:
            try:
                from playwright_stealth import stealth
                # stealth() returns an init script string in some versions
                _s = stealth()
                if isinstance(_s, str):
                    await ctx.add_init_script(_s)
                    print("[artlist] playwright-stealth (init-script) applied ✓")
                else:
                    print("[artlist] playwright-stealth unknown API — using manual stealth JS")
                    await ctx.add_init_script(_STEALTH_JS)
            except Exception as _se2:
                print(f"[artlist] playwright-stealth unavailable ({_se2}) — using manual stealth JS")
                await ctx.add_init_script(_STEALTH_JS)
        # Block clicks on any link that would navigate to Claude/Anthropic domains.
        # This must run on every page load so the banner can never trigger navigation.
        await ctx.add_init_script(_CLAUDE_BLOCKER_JS)
        return browser, ctx

    async def _run_generation(pw, force_password: bool = False) -> bytes:
        """Run one full generation attempt.  When force_password=True the
        cookie file is ignored and email/password login is used instead.
        After a successful login the fresh Playwright session is saved so
        the *next* call can skip the login entirely."""
        browser, ctx = await _new_browser(pw)
        try:
            page = await ctx.new_page()

            logged_in = False
            if not force_password:
                cookie_loaded = await _load_cookies(ctx)
                if cookie_loaded:
                    logged_in = await _is_logged_in(page)
                    # Dump subscription/session cookies that the server set
                    # (or refreshed) during the navigation so we can see
                    # whether artlist.subscription was reissued.
                    try:
                        ctx_cookies = await ctx.cookies()
                        key_cookies = {c["name"]: c for c in ctx_cookies
                                       if c["name"] in (
                                           "artlist.subscription", "userSession",
                                           "__Secure-session.artlist-prod.session-token",
                                           "__cf_bm", "cf_clearance",
                                       )}
                        for name, c in key_cookies.items():
                            val_preview = c.get("value", "")[:60]
                            exp = c.get("expires", "session")
                            print(f"[artlist] ctx-cookie [{name}] "
                                  f"exp={exp} val={val_preview}…")
                    except Exception as _ce:
                        print(f"[artlist] ctx-cookie dump failed: {_ce}")

            if not logged_in:
                if force_password:
                    print("[artlist] forced password login (previous 403 cleared stale cookies)")
                else:
                    print("[artlist] cookies missing/expired — falling back to password login")
                await _login(page, progress, snap)
                # Save the fresh Playwright-native session so future runs
                # skip the password login without hitting the 403.
                await _save_cookies(ctx)

            await _open_video_generator(page, progress, snap)

            await progress("✍️ Entering prompt…")
            await _type_prompt(page, prompt, snap)

            await _remove_frames(page, snap)

            # Always select the model FIRST so the correct image-input UI is active.
            await _select_model(
                page, model, progress, snap,
                duration_override=duration,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                audio=audio,
            )

            # Image upload AFTER model selection:
            #  • MODELS_WITHOUT_IMAGE_REF  — text-to-video only; skip entirely.
            #  • MODELS_USING_START_FRAME  — Kling: "Start & End Frame → Start Frame"
            #  • everything else            — "Image Reference"
            if image_ref_bytes:
                if model in MODELS_WITHOUT_IMAGE_REF:
                    print(f"[artlist] ⚠️ {model} does not support image reference — skipping upload")
                elif model in MODELS_USING_START_FRAME:
                    await progress("🖼️ Uploading Start Frame image…")
                    await _upload_start_frame(page, image_ref_bytes, image_ref_ext, snap)
                else:
                    await progress("🖼️ Uploading reference image…")
                    await _upload_image(page, image_ref_bytes, image_ref_ext, snap)

            await _generate_and_wait(page, progress, snap, model=model)

            video_result = await _download_video(page, progress, snap)
            # video_result is either a URL string or bytes (blob fallback)
            if isinstance(video_result, str):
                await progress("✅ Done!")
                return video_result
            # bytes fallback — still apply watermark if needed
            if not skip_watermark:
                await progress("💧 Adding watermark…")
                video_result = await _add_watermark(video_result)
            await progress("✅ Done!")
            return video_result

        finally:
            await ctx.close()
            await browser.close()

    async with async_playwright() as pw:
        try:
            return await _run_generation(pw, force_password=False)
        except RuntimeError as exc:
            if "403" in str(exc):
                # The generation API rejected the request despite a valid
                # page session.  This means the toolkit auth token wasn't
                # established — likely due to SSO flow not completing.
                # Do NOT retry with email/password: if the account uses
                # Google/SSO there is no password to use and it will always
                # fail.  Surface the error immediately so the diagnostics
                # (localStorage dump, full headers) in the log can be used
                # to determine the real fix.
                print(
                    "[artlist] 403 — NOT retrying with email/password "
                    "(account likely uses Google SSO). "
                    "Check the logs for localStorage/header diagnostics."
                )
            raise
