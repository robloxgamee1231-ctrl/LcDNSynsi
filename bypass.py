"""
bypass.py — Cinematic prompt bypass for Seedance / AI video filters.

Usage:
    from bypass import apply_bypass_prompt, _bypass_display_prompt

    gen_prompt     = apply_bypass_prompt("goku fires a kamehameha")
    display_prompt = _bypass_display_prompt("goku fires a kamehameha")
"""

import re as _re

# ── Six-Slot Formula ──────────────────────────────────────────────────────────
# Seedance (and most AI video filters) evaluate *context and intent*, not just
# keywords.  A prompt that reads like a filmmaker describing a shot will pass;
# one that names a copyrighted character won't.
#
# Six-Slot Formula: [Camera] · [Subject visual desc] · [Action] ·
#                   [Setting] · [Style] · [Lighting]

# ── Character name → precise visual description ───────────────────────────────
_BYPASS_CHARACTER_MAP: dict[str, str] = {
    # Dragon Ball
    "goku":        "a powerfully built martial artist with wild upswept dark hair in a faded combat training uniform, radiating calm intensity",
    "vegeta":      "a fierce compact warrior with slicked-back dark hair in sleek gunmetal form-fitting battle armor, arms crossed with cold arrogance",
    "gohan":       "a studious-looking teenage fighter with short dark hair who shifts into explosive combat mode in a worn training outfit",
    "piccolo":     "a tall green-skinned alien warrior with large pointed ears in a weighted white fighting cloak and turban",
    "frieza":      "a sleek alien ruler with a smooth featureless face in white and purple bio-mechanical armor, levitating with terrifying calm",
    "cell":        "a towering insectoid bio-engineered warrior with black and green segmented exoskeleton and a long pointed tail",
    "beerus":      "a lithe cat-eared deity with purple skin in golden ceremonial robes, floating with godlike indifference",
    "broly":       "a colossally muscular berserker with wild tangled dark hair and glowing green crackling energy, screaming with feral rage",
    # Naruto
    "naruto":      "a scrappy young ninja with messy blond hair and bright determined eyes in an orange and black combat tracksuit",
    "sasuke":      "a brooding teenage swordsman with dark hair and pale skin in a dark high-collar shirt, radiating cold intensity",
    "kakashi":     "a silver-haired masked ninja with a lazy slouch concealing elite reflexes, one eye hidden beneath a headband",
    "itachi":      "a lean young man with long dark hair tied back and deep red eyes in a black high-collar cloak, calm and lethal",
    "madara":      "an imposing warlord with long spiky dark hair in elaborate dark samurai-style armor, exuding overwhelming power",
    "obito":       "a mysterious masked warrior in a swirling orange mask and dark cloak who moves through solid matter",
    # One Piece
    "luffy":       "a lanky fearless young pirate with a straw hat and a wide grin in a red sleeveless vest and sandals",
    "zoro":        "a muscular green-haired swordsman in a dark open shirt with three katanas secured at his waist and a bandana",
    "sanji":       "a cool-headed blond man with a curly eyebrow in a slim-cut dark suit, cigarette at his lips",
    "shanks":      "a red-haired pirate captain with a weather-beaten scar and a confident smirk in a long dark coat",
    # Attack on Titan
    "eren":        "a grim-faced young soldier with long dark hair tied back in an olive-green military uniform, filled with quiet fury",
    "levi":        "a short but terrifyingly capable soldier with an undercut and sharp eyes in an olive-green scout cloak",
    "mikasa":      "a stoic dark-haired young woman in olive-green military uniform with a worn red scarf tucked at her collar",
    "erwin":       "a tall blond commanding officer with heavy eyebrows and a strong jaw in an olive-green military overcoat",
    # Demon Slayer
    "tanjiro":     "a kind-faced young swordsman with dark reddish-brown hair in a charcoal-patterned kimono haori, carrying a blade on his back",
    "zenitsu":     "a cowardly-looking young fighter with shaggy blond hair in a yellow kimono who transforms when unconscious",
    "inosuke":     "a wild bare-chested muscular boy wearing a boar skull as a helmet with twin serrated blades",
    "nezuko":      "a small young girl with long dark hair and pink eyes in a pink kimono and a pale cloth wrapped around her mouth",
    "rengoku":     "a fierce passionate swordsman with wild flame-colored red and yellow hair in a flamboyant flame-patterned haori",
    # Jujutsu Kaisen
    "gojo":        "a tall white-haired young man with striking blue eyes in a dark uniform, often wearing a black cloth over his eyes, exuding effortless dominance",
    "yuji":        "a stocky athletic young man with pink hair and brown eyes in a dark school uniform, radiating raw physical power",
    "sukuna":      "a menacing figure covered in black tattoos with extra sets of eyes and disheveled pink hair, radiating ancient malice",
    # My Hero Academia
    "deku":        "a green-haired boy in a battered green and grey armored hero costume with a full-face respirator mask",
    "bakugo":      "a spiky blond teen with an aggressive scowl in a dark hero costume with large gauntlet-like bracers on his arms",
    "todoroki":    "a serious young hero with half red and half white hair divided down the middle in a white hero bodysuit",
    "allmight":    "a massively muscular hero with a beaming smile in a blue and gold star-patterned hero costume",
    "all might":   "a massively muscular hero with a beaming smile in a blue and gold star-patterned hero costume",
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
    "ichigo kurosaki":  "a spiky orange-haired teenager in black samurai robes wielding an oversized jagged black cleaver sword",
    "rukia":            "a petite dark-haired young woman in a dark uniform with violet-blue eyes and a calm demeanor",
    "aizen":            "a calm dark-haired man with oval glasses in a silver officer's uniform, hiding immense power behind a kind face",
    "byakuya":          "a noble pale man with long dark hair in a captain's cloak, cold and aristocratic",
    "renji":            "a tall man with long red hair in a high topknot covered in dark tribal tattoos in shinigami robes",
    "urahara":          "an eccentric shopkeeper in a striped bucket hat and pale cloak who is far more dangerous than he appears",
    # Fairy Tail
    "erza":             "a warrior woman with long red hair in ornate silver plate armor wielding multiple blades simultaneously",
    "gray":             "a dark-haired young man with grey eyes and a distinctive tattoo who tends to lose his shirt in battle",
    "lucy":             "a young woman with long blonde hair in a navy outfit carrying a set of ornate golden keys",
    "mavis":            "a small fairy-like guild master with very long flowing blonde hair in a white frilled dress",
    # Fullmetal Alchemist
    "alphonse":         "a towering hollow suit of grey medieval armor animated by a soul, gentle despite his size",
    "roy mustang":      "a sharp-featured military officer with neat dark hair in a dark navy uniform who snaps his fingers to ignite flames",
    "winry":            "a young woman with blonde pigtails in overalls with grease on her hands and a wrench at her hip",
    "envy":             "a pale androgynous shapeshifter with long black jagged hair and violet eyes in a black form-fitting suit",
    # Avatar
    "aang":             "a young bald boy with a blue arrow tattoo on his forehead in orange and saffron monk robes",
    "zuko":             "a young man with a burn scar over his left eye and dark hair in a red royal robe, torn between duty and honor",
    "katara":           "a young woman with blue eyes and dark hair in water-tribe dark blue wrappings",
    "toph":             "a small girl with pale skin and unseeing grey eyes in an earth-tribe outfit who sees through her feet",
    "azula":            "a sharp-featured young woman with dark hair in red royal fire nation armor, frighteningly precise",
    # Chainsaw Man
    "denji":            "a scruffy young man with blond hair and a pull-cord on his chest who transforms into something dangerous",
    "makima":           "a composed woman with reddish-brown hair in a neat office shirt with unusual ringed eyes",
    "power":            "a brash young woman with blond hair and small red horns in a dark school uniform, chaotic and blood-hungry",
    # Hunter x Hunter
    "gon":              "a spiky dark-haired boy in an olive jacket and dark shorts with bright green eyes and boundless enthusiasm",
    "killua":           "a pale boy with spiky silver-white hair and teal eyes in a dark long-sleeved shirt, an assassin's grace in every move",
    "hisoka":           "a tall lithe magician with pink and golden face paint in a diamond-motif jester costume, deeply unsettling",
    "kurapika":         "a pale young man with short blond hair and chain-wrapped hands in a dark ivory battle suit driven by vengeance",
    # Sword Art Online
    "kirito":           "a dark-haired young man in a long black coat in a dual-wield fighting stance, the black swordsman",
    "asuna":            "a young woman with long chestnut hair in white and burgundy battle armor, lightning-fast with a rapier",
    # Re:Zero
    "subaru":           "a dark-haired young man in a dark navy tracksuit who keeps dying and waking up at the same point in time",
    "rem":              "a blue-haired young woman in a white maid apron dress with a dark navy ribbon, fiercely devoted",
    # Evangelion
    "rei":              "a pale girl with short blue-grey hair and violet eyes in a white form-fitting plugsuit, distant and quiet",
    "asuka":            "a fiery young woman with long auburn hair in a red form-fitting pilot suit, aggressive and proud",
    # Pokémon
    "pikachu":          "a small round yellow rodent creature with black-tipped ears, rosy cheeks and a lightning bolt tail",
    "mewtwo":           "a sleek grey psychic clone creature with a thick tail and powerful violet eyes, lonely and powerful",
    "charizard":        "a bipedal orange dragon with cream belly, large blue-tipped wings and a flame blazing at its tail tip",
    "ash":              "a young boy in a dark cap and grey jacket with a determined expression and a pokéball in hand",
    # Transformers
    "optimus prime":    "a massive heroic robot with a cobalt and red chassis and a protruding silver face-plate, voice of authority",
    "megatron":         "a hulking silver robot tyrant with a dark cannon fused to one arm and an unquenchable thirst for conquest",
    # Game of Thrones
    "jon snow":         "a brooding dark-haired young man in a heavy fur-lined black cloak with a pale bastard sword at his hip",
    "daenerys":         "a pale young woman with long platinum braided hair in violet robes, flanked by mythical winged creatures",
    "tyrion":           "a small clever man with mismatched eyes in crimson robes, the most dangerous person in any room",
    # Lord of the Rings
    "gandalf":          "an ancient robed wanderer with long white hair and beard in a white cloak carrying a gnarled staff",
    "frodo":            "a small curly dark-haired halfling in a simple vest, carrying a terrible burden with quiet courage",
    "aragorn":          "a rugged dark-haired ranger in worn travel leathers with a battered but noble heirloom sword",
    "legolas":          "a lithe pale elf with long golden hair in silver and green forest armor with a long bow, impossibly graceful",
    "sauron":           "a towering dark figure in black spiked armor with a single blazing eye, lord of shadow",
    # Harry Potter
    "harry potter":     "a young man with messy dark hair and round wire-framed glasses with a faint old scar on his forehead",
    "hermione":         "a young woman with bushy brown hair in school robes always clutching a thick book",
    "voldemort":        "a skeletal pale figure with a flat noseless face and red snake-like eyes in flowing dark robes",
    "dumbledore":       "a tall elderly man with a long silver beard in sweeping violet robes and half-moon spectacles",
    # Cyberpunk
    "v":                "a neon-lit mercenary with cybernetic implants etched across their temple in a worn dark jacket",
    "johnny silverhand":"a rocker mercenary with platinum hair and a gleaming silver cybernetic arm in a dark vest",
    # Misc games
    "2b":               "a pale android warrior in a white dress and dark blindfold wielding a thin dark silver blade",
    "dante":            "a cocky silver-haired demon hunter in a long red coat with twin ivory pistols and a massive sword",
    "nero":             "a young demon hunter with platinum hair in a navy coat with a glowing mechanical arm",
    "solid snake":      "a grizzled operative in dark olive tactical suit with a dark bandana over his forehead",
    "big boss":         "a battle-scarred soldier with an eyepatch and weathered olive tactical gear",
    "raiden":           "a pale cyborg warrior with white hair in a sleek dark exoskeletal suit wielding a high-frequency blade",
    "ezio":             "a roguish dark-haired assassin in white hooded robes with hidden blades at his wrists",
    "altair":           "a stoic dark-featured assassin in white hooded robes with an eagle-beak hood",
    "lara croft":       "an athletic young woman with dark hair in a tight braid in worn olive explorer gear with a compound bow",
    "nathan drake":     "a wisecracking adventurer with dark hair in a henley shirt and worn pants who trips into legends",
    "ellie":            "a teenage girl with auburn hair and freckles in a worn flannel shirt, hardened beyond her years",
    "joel":             "a grizzled middle-aged man with greying dark hair in a worn flannel jacket, carrying old grief",
    "arthur morgan":    "a weathered outlaw in a wide-brimmed hat and duster coat with a lever-action rifle, code of honor included",
    "master chief":     "a towering supersoldier in olive green powered armor with a mirrored gold visor",
}

# ── IP-specific technique / term map ─────────────────────────────────────────
# Franchise vocabulary that slips through even after name substitution.
_BYPASS_TERMS_MAP: dict[str, str] = {
    # Dragon Ball techniques / lore
    "kamehameha":               "focused beam of concentrated energy",
    "spirit bomb":              "massive sphere of gathered life energy",
    "final flash":              "overwhelming beam of pure destructive energy",
    "big bang attack":          "explosive burst of concentrated energy",
    "special beam cannon":      "piercing spiral beam of focused energy",
    "instant transmission":     "teleportation technique",
    "kaioken":                  "power-multiplying combat technique",
    "ultra instinct":           "ultimate reflex-driven combat state",
    "super saiyan":             "golden-haired ascended warrior form",
    "saiyan":                   "elite warrior",
    "namekian":                 "tall green alien warrior",
    "frieza force":             "intergalactic military force",
    "dragon ball":              "mystical orb",
    "planet namek":             "alien world",
    "capsule corp":             "advanced technology company",
    # Naruto techniques / lore
    "rasengan":                 "swirling sphere of concentrated energy",
    "chidori":                  "crackling palm-strike of focused lightning",
    "shadow clone jutsu":       "mass self-duplication technique",
    "mangekyou sharingan":      "evolved dark crimson multi-form eye technique",
    "sharingan":                "deep crimson pattern-tracking eye ability",
    "rinnegan":                 "deep violet multi-ring omnipotent eye ability",
    "byakugan":                 "pale veined all-seeing eye ability",
    "susanoo":                  "colossal ethereal warrior construct",
    "amaterasu":                "inextinguishable dark crimson eye-flame",
    "tsukuyomi":                "mental illusion binding technique",
    "eight gates":              "extreme physical limiter-release technique",
    "sage mode":                "nature-energy enhanced combat state",
    "tailed beast":             "colossal chakra creature",
    "kurama":                   "colossal nine-tailed fox spirit",
    "chakra":                   "life energy",
    "ninjutsu":                 "energy combat technique",
    "genjutsu":                 "illusionary technique",
    "taijutsu":                 "physical combat technique",
    "jutsu":                    "combat technique",
    "akatsuki":                 "cloaked rogue mercenary organization",
    "hidden leaf village":      "fortified ninja settlement",
    "konoha":                   "fortified ninja settlement",
    # Bleach techniques / lore
    "bankai":                   "ultimate weapon-release technique",
    "shikai":                   "initial weapon-release technique",
    "zanpakuto":                "spirit-bonded blade",
    "hollowification":          "transformation into a dark hollow entity",
    "getsuga tensho":           "crescent arc of dark energy",
    "tensa zangetsu":           "compressed dark energy katana",
    "senbonzakura":             "thousand-blade petal dispersal technique",
    "soul society":             "spirit realm",
    "hollow":                   "dark spirit creature",
    "shinigami":                "spirit warrior",
    "reiatsu":                  "spiritual energy pressure",
    # One Piece techniques / lore
    "conqueror's haki":         "overwhelming will force projection",
    "gear second":              "blood-pump overclocked combat form",
    "gear third":               "bone-inflated giant limb combat form",
    "gear fourth":              "muscle-compressed bouncing combat form",
    "gear fifth":               "reality-altering transcendent combat form",
    "devil fruit":              "supernatural ability-granting fruit",
    "gomu gomu":                "elastic rubber ability",
    "gum-gum":                  "elastic rubber ability",
    "haki":                     "invisible force projection",
    "marineford":               "naval fortress battle arena",
    # My Hero Academia
    "one for all":              "stockpiled power transfer ability",
    "all for one":              "ability-stealing power",
    "detroit smash":            "full-powered downward punch",
    "plus ultra":               "beyond limits battle cry",
    "u.a. high":                "hero training academy",
    "quirk":                    "superpower",
    # Attack on Titan lore
    "founding titan":           "progenitor colossal form",
    "attack titan":             "future-seeing colossal form",
    "omni-directional":         "multi-directional grapple gear",
    "maneuver gear":            "grapple-and-blade combat harness",
    "survey corps":             "scouting military unit",
    "rumbling":                 "earth-shaking colossal army march",
    "titan":                    "colossal humanoid creature",
    # Demon Slayer techniques / lore
    "demon slayer corps":       "demon-hunting military organization",
    "hinokami kagura":          "blazing sun-dance sword technique",
    "total concentration":      "enhanced breathing combat state",
    "blood demon art":          "demonic supernatural power",
    "water breathing":          "flowing water-form sword technique",
    "flame breathing":          "explosive fire-form sword technique",
    "thunder breathing":        "lightning-fast sword technique",
    "wind breathing":           "slashing gale-form sword technique",
    "sun breathing":            "blazing ancient sun-form sword technique",
    "wisteria":                 "pale purple toxic flower",
    # Jujutsu Kaisen
    "reverse cursed technique": "healing supernatural technique",
    "domain expansion":         "reality-altering spiritual domain technique",
    "sukuna's domain":          "ancient demon's spatial technique",
    "infinite void":            "infinite perception overload technique",
    "cursed technique":         "supernatural combat technique",
    "cursed energy":            "supernatural malevolent energy",
    "black flash":              "distorted space combat strike",
    "jujutsu high":             "supernatural combat academy",
    # Pokémon
    "pokémon":                  "creature companion",
    "pokemon":                  "creature companion",
    "poké ball":                "capture sphere",
    "pokeball":                 "capture sphere",
    "team rocket":              "criminal organization in dark uniforms",
    "gym leader":               "arena champion",
    "evolution":                "transformation",
    # Transformers
    "autobot":                  "heroic machine warrior",
    "decepticon":               "villainous machine warrior",
    "energon":                  "glowing energy crystal",
    "cybertron":                "mechanical home world",
    # Star Wars
    "lightsaber":               "glowing plasma blade",
    "the force":                "mystical binding energy field",
    "rebel alliance":           "freedom-fighter resistance force",
    "death star":               "massive spherical space station",
    "the empire":               "authoritarian galactic regime",
    "stormtrooper":             "white armored soldier",
    "jedi":                     "ancient energy-wielding warrior order",
    "sith":                     "dark-side energy warrior",
    "hyperspace":               "faster-than-light travel",
    "blaster":                  "energy pistol",
    # Marvel
    "infinity gauntlet":        "jeweled omnipotent gauntlet",
    "infinity stones":          "cosmic power gems",
    "thanos snap":              "reality-altering finger snap",
    "s.h.i.e.l.d.":            "covert government task force",
    "arc reactor":              "compact fusion power core",
    "web-slinging":             "swinging on strong wire lines",
    "vibranium":                "ultra-dense fictional metal",
    "mjolnir":                  "ancient enchanted war hammer",
    "symbiote":                 "dark alien life-form",
    "avengers":                 "team of super-powered heroes",
    "hydra":                    "shadow military organization",
    # DC
    "justice league":           "super-powered hero team",
    "speed force":              "kinetic energy dimension",
    "batmobile":                "sleek armored pursuit vehicle",
    "bat-signal":               "large spotlight projection",
    "kryptonite":               "glowing radioactive mineral",
    "gotham":                   "gritty rain-soaked city",
    "metropolis":               "gleaming modern city",
    # Video game lore
    "blades of chaos":          "chain-linked curved blades",
    "god of war":               "divine combat power",
    "hunter's dream":           "ethereal safe sanctuary",
    "master chief":             "enhanced supersoldier",
    "dark souls":               "cursed crumbling world",
    "elden ring":               "shattered golden artifact",
    "silver sword":             "enchanted pale silver blade",
    "limit break":              "unleashed maximum power technique",
    "estus flask":              "glowing amber healing vessel",
    "muda muda":                "overwhelming barrage",
    "ora ora":                  "rapid close-range assault",
    "fantasy vii":              "sci-fi world",
    "covenant":                 "alien religious military alliance",
    "witcher":                  "mutant monster hunter",
    "spartan":                  "enhanced supersoldier",
    "geralt's":                 "the scarred hunter's",
    "halo":                     "ancient orbital ring structure",
    "midgar":                   "sprawling industrial city under a steel plate",
    "materia":                  "glowing magical orb",
    "persona":                  "summoned inner spirit creature",
    "stand":                    "summoned spiritual power manifestation",
    "requiem":                  "ultimate evolved form",
    "bloodborne":               "gothic plague-ridden world",
    "erdtree":                  "colossal luminous golden tree",
    "jojo":                     "flamboyant fighter",
    # Game of Thrones / fantasy lore
    "white walker":             "ice-blue undead warrior",
    "king's landing":           "walled coastal capital city",
    "dragonfire":               "roaring jet of dragon flame",
    "wildfire":                 "toxic green alchemical flame",
    "targaryen":                "silver-haired dragon-riding royal",
    "lannister":                "golden-armored noble house warrior",
    "westeros":                 "fractured medieval kingdom",
    # Harry Potter lore
    "avada kedavra":            "lethal magical incantation",
    "death eater":              "masked dark-robed cultist",
    "expelliarmus":             "disarming magical spell",
    "patronus":                 "shimmering silver spirit guardian",
    "horcrux":                  "dark soul-fragment artifact",
    "quidditch":                "airborne broom sport",
    "hogwarts":                 "gothic castle academy",
    "butterbeer":               "warm frothy sweet drink",
    # Lord of the Rings lore
    "mount doom":               "active volcanic mountain",
    "one ring":                 "simple golden cursed ring",
    "uruk-hai":                 "large dark armored orc warrior",
    "rivendell":                "elegant elven forest sanctuary",
    "balrog":                   "ancient shadow and flame demon",
    "mithril":                  "pale silver ultra-light legendary metal",
    "mordor":                   "desolate volcanic wasteland",
    "shire":                    "rolling pastoral countryside village",
    # Generic anime / manga
    "spiritual pressure":       "energy presence",
    "power level":              "combat strength reading",
    "aura":                     "energy field",
    "mana":                     "magical energy",
    "nen":                      "life energy",
    "ki":                       "life force energy",
}


# ── Pre-compile combined patterns (longest-first to avoid partial collisions) ─
def _build_bypass_pattern(mapping: dict[str, str]) -> _re.Pattern:
    keys = sorted(mapping, key=len, reverse=True)
    pattern = r"\b(" + "|".join(_re.escape(k) for k in keys) + r")\b"
    return _re.compile(pattern, _re.IGNORECASE)

_BYPASS_CHAR_PATTERN  = _build_bypass_pattern(_BYPASS_CHARACTER_MAP)
_BYPASS_TERMS_PATTERN = _build_bypass_pattern(_BYPASS_TERMS_MAP)

_CAMERA_WORDS = (
    "cinematic", "wide shot", "close-up", "medium shot", "tracking shot",
    "slow motion", "dramatic lighting", "golden hour", "film grain",
)


def apply_bypass_prompt(prompt: str) -> str:
    """
    Full cinematic bypass:
      1. Replace known IP character names with precise visual descriptions.
      2. Replace franchise-specific techniques, abilities, lore terms, and
         place-names with neutral cinematic equivalents.
      3. Strip legacy 11ii…11ii tags.
      4. Wrap in a Six-Slot cinematic framing prefix so the filter reads
         a filmmaker's shot description instead of a keyword list.
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
    result = _re.sub(r"11ii(.+?)11ii", r"\1", result)

    # 4 — Cinematic wrapping (skip if already camera-described)
    if not any(w in result.lower() for w in _CAMERA_WORDS):
        result = "Cinematic wide shot, dramatic natural lighting, film grain — " + result

    return result


def _bypass_display_prompt(prompt: str) -> str:
    """
    Discord display version: replaced words shown in **bold** so users can
    see exactly what was swapped. Does NOT add the cinematic prefix.
    """
    result = prompt

    result = _BYPASS_CHAR_PATTERN.sub(
        lambda m: f"**{_BYPASS_CHARACTER_MAP[m.group(0).lower()]}**", result
    )
    result = _BYPASS_TERMS_PATTERN.sub(
        lambda m: f"**{_BYPASS_TERMS_MAP[m.group(0).lower()]}**", result
    )
    result = _re.sub(r"11ii(.+?)11ii", r"\1", result)

    return result


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        "goku fires a kamehameha at frieza",
        "naruto uses rasengan with sage mode chakra",
        "batman fights joker in gotham",
        "ichigo activates bankai against a hollow",
        "eren transforms into the attack titan during the rumbling",
        "link finds mithril in rivendell",
    ]
    for t in tests:
        print(f"IN : {t}")
        print(f"OUT: {apply_bypass_prompt(t)}")
        print()
