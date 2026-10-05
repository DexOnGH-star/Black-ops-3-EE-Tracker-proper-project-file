"""
BO3 Easter Egg Tracker - a standalone Windows timer + checklist for Black Ops 3
Zombies Easter eggs. It does NOT touch the game in any way: it's just a normal
window with a stopwatch and a checklist.

  * pick a map, press Start, and check off each step as you go
  * every step gets its own timer, plus an overall timer for the whole egg
  * press "Finish Easter Egg" when you're done to stop the overall timer
  * personal best, best segments, and run history are saved per map
  * hotkeys work even while the game is in focus:
        F6  start / complete current step / finish
        F7  undo last step
        F8  pause / resume

Needs only Python 3.9+ (tkinter ships with the Windows installer).
Data is saved to %APPDATA%\\BO3EETracker\\data.json
"""
import datetime as dt
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from xml.sax.saxutils import escape

FONT = "Segoe UI"
MONO = "Consolas"
EMOJI = "Segoe UI Emoji"

BASE = {"amber": "#FFB020", "red": "#FF5A5A", "gold": "#F6C945", "blood": "#8E1F27", "good": "#4CE06B"}

THEMES = {
    "Blood Red": {
        "side": "#080405", "side_hover": "#1A0C0E", "bg": "#0D0707", "card": "#170D0E",
        "card2": "#221213", "border": "#3B1D20", "text": "#F3E7E7", "muted": "#A58889",
        "green": "#E5333A", "green_dark": "#C42229", "on_green": "#FFFFFF",
    },
    "Toxic Green": {
        "side": "#070A08", "side_hover": "#131D16", "bg": "#0C110E", "card": "#141D17",
        "card2": "#1B281F", "border": "#26382C", "text": "#E8F3EA", "muted": "#7F9687",
        "green": "#4CE06B", "green_dark": "#35B853", "on_green": "#06130A",
    },
    "Amber": {
        "side": "#0A0805", "side_hover": "#1B150B", "bg": "#100C07", "card": "#1A140C",
        "card2": "#241C10", "border": "#41331C", "text": "#F3EBDD", "muted": "#9C8E76",
        "green": "#FFB020", "green_dark": "#D99412", "on_green": "#1A1000",
    },
    "Ice Blue": {
        "side": "#04080C", "side_hover": "#0E1822", "bg": "#070D13", "card": "#0E1822",
        "card2": "#14222F", "border": "#223A4E", "text": "#E6F1F8", "muted": "#7D97A8",
        "green": "#3DB5FF", "green_dark": "#2593D6", "on_green": "#02131E",
    },
    "Violet": {
        "side": "#08050C", "side_hover": "#16101F", "bg": "#0D0912", "card": "#16101F",
        "card2": "#1F1730", "border": "#35264F", "text": "#EEE8F6", "muted": "#9387A8",
        "green": "#A66BFF", "green_dark": "#8A4EE6", "on_green": "#FFFFFF",
    },
}
C = {**BASE, **THEMES["Blood Red"]}
TIMER_SCALE = 1.0
SHOW_CS = True
HK_ROLES = ("main", "undo", "pause", "overlay")
HK_DEFAULTS = {"main": "F6", "undo": "F7", "pause": "F8", "overlay": "F9"}
HK = {"F6": "F6", "F7": "F7", "F8": "F8", "F9": "F9"}   # how the default key names are shown
KEY_CHOICES = {f"F{i}": (0x6F + i, f"F{i}") for i in range(1, 13)}
KEY_CHOICES.update({f"Num {i}": (0x60 + i, f"KP_{i}") for i in range(10)})
TIMER_FONTS = ["Consolas", "Cascadia Mono", "Courier New", "Lucida Console", "Segoe UI"]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def shade(c, f):
    return _hex([v * f for v in _rgb(c)])


def lum(c):
    r, g, b = _rgb(c)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def apply_theme(s):
    # Applies theme, custom accent, timer font/size, hundredths and hotkey labels.
    global MONO, TIMER_SCALE, SHOW_CS
    C.clear()
    C.update(BASE)
    C.update(THEMES.get(s.get("theme"), THEMES["Blood Red"]))
    a = s.get("accent")
    if a:
        C["green"], C["green_dark"] = a, shade(a, 0.82)
        C["on_green"] = "#000000" if lum(a) > 0.55 else "#FFFFFF"
    MONO = s.get("timer_font") or "Consolas"
    TIMER_SCALE = float(s.get("timer_scale", 1.0))
    SHOW_CS = bool(s.get("show_cs", True))
    for role, key in zip(HK_ROLES, ("F6", "F7", "F8", "F9")):
        HK[key] = s.get("hk_" + role) or HK_DEFAULTS[role]


def hk(text):
    # swaps the default key names in a label for whatever keys you've chosen
    return re.sub(r"F[6-9]", lambda m: HK.get(m.group(0), m.group(0)), text)


def tsz(n):
    return max(8, int(n * TIMER_SCALE))


# ---------------------------------------------------------------- built-in maps
def _steps(rows):
    return [{"name": n, "note": t} for n, t in rows]


DEFAULT_MAPS = [
    {"id": "soe", "name": "Shadows of Evil", "steps": _steps([
        ("Complete the four Rituals",
         "Find the Summoning Key and the four ritual items (badge, toupee, belt, pen). Start a ritual in each district and pick up the Gateworm each one drops."),
        ("Enter the Rift and unlock Pack-a-Punch",
         "Beast mode through the canal door into the Rift, use the wall of yellow symbols, then place a Gateworm in all four fountains."),
        ("Get the Apothicon Sword",
         "Note the three red train symbols, zap them in the Rift, take the Egg from the throne, charge it at all four octopus statues, bring it back."),
        ("Upgrade the Sword",
         "Take the Arch-Ovum from your ritual ghost, place it in each of the four districts, kill the Margwa at each, then return to the ghost."),
        ("Flag 1: protect it and give it to a ghost",
         "Raise the book in Nero's library first. Grab the flag from the subway, plant it outside, defend it, then return it to the ghost."),
        ("Flag 2", "A new flag spawns after the round ends. Repeat for another character."),
        ("Flag 3", "Same again for the next character."),
        ("Flag 4", "Last character. A Civil Protector helps a lot here."),
        ("Kill the Shadow Man",
         "Finish a round inside the Pack-a-Punch cave, then activate each ghost to drop his shield. Repeat the phases until he's captured."),
        ("Co-op: unlimited Beast Mode + Giant Gateworm",
         "Kill purple-eyed Margwas for unlimited Beast Mode fountains. The giant Gateworm now sits above the Junction crane."),
        ("Co-op: electrify stations and Gatekeepers",
         "Call the train into the worm. Shock the panels at the three train stations, then all three Gatekeepers at the Junction."),
    ])},
    {"id": "de", "name": "Der Eisendrache", "steps": _steps([
        ("Build Pack-a-Punch", "Teleport to collect the missing pieces. Use it afterwards by teleporting to the Rocket Launch site."),
        ("Activate the pyramid", "Under the first dragon: stand about 5 seconds on each of the four blue squares until the pyramid lights up."),
        ("Build the Wrath of the Ancients", "Feed all four wall dragons zombies, then finish the bow."),
        ("Upgrade the bow", "One elemental bow per player is enough. Solo, the electric bow is the usual pick."),
        ("Build the Ragnarok DG-4", "Assemble its three parts at a workbench."),
        ("Use the DG-4 on the Clock Tower",
         "Plant it behind the clock and stop the clock at 9:35 with the lever downstairs. Some players report this step isn't required."),
        ("Shoot the wisps",
         "Turn the teleporter prongs gold with the basic bow, then hit four blue sparks in quick succession. Don't let the round end."),
        ("Go back in time", "Take the purple teleporter. Grab the Soul Canister and blue fuse, and note Groph's safe code."),
        ("Death Ray: fuse, PROTECT mode, enter code",
         "Insert the blue fuse, set PROTECT, then type the code at the Clock Tower terminal. The code changes each round, so don't kill the last zombie."),
        ("Open the safe and charge the Death Ray",
         "Take the tesla coils and floppy disk, put a coil either side, set DESTROY and fire, then insert the disk."),
        ("Simon Says (both terminals)",
         "Memorise the four lower symbols and match each top symbol. Do it at the Clock Tower and at the Rocket Launch terminal."),
        ("Bring down Dempsey's pod", "Fire the Death Ray again with the green button on the back."),
        ("Golden rod, keeper tablet and ritual circles",
         "Repeat the wisps, travel back, take the keeper tablet. Slot the golden rod in the tomb and charge three circles with the matching bow."),
        ("Defeat the Keeper boss",
         "Put the Soul Canisters in the pyramid corners and DG-4s on the pressure plates. Trap him with a DG-4 during his lightning, then hit him with bows."),
        ("Insert the Summoning Key", "Pick up the key and put it into the Clock Tower terminal."),
    ])},
    {"id": "zns", "name": "Zetsubou No Shima", "steps": _steps([
        ("Turn on all the generators", "Power the whole map."),
        ("Claim the Skull of Nan Sapwe",
         "Take each black skull to the altar with the matching symbol, defend it, return it. After all four, kill the Keepers and collect the weapon underground."),
        ("Complete the ritual pillar challenges", "Three tiers: max ammo, heavy weapon, then an extra perk slot."),
        ("Repair the pumps to open Pack-a-Punch", "Collect the three machine parts. Not required, but you'll want it."),
        ("Build the KT-4", "Three parts, built at the special workbench underground."),
        ("Build and electrify the zombie shield",
         "Equip it near the ritual site, wait for lightning, and walk through the aftermath."),
        ("Kill the Spider boss", "Melt the web behind the 115 water in Lab A with the KT-4, go underground, shoot the spider in the mouth."),
        ("Scan the building plans poster", "Use the Skull on the wall in the purple-115 room inside the bunker."),
        ("Reveal the secret elevator room", "Scan the left wall in the bunker's first room with the Skull."),
        ("Upgrade the KT-4 to the Masamune", "Collect the rainbow water, spider venom and skeleton test tube parts."),
        ("Find the three cogs",
         "Dragon room (Anywhere But Here gum), the Docks (electric shield on the panel, zip line drop), and the plane (grow the plant, AA shell, shoot the plane)."),
        ("Repair the elevator and descend", "Insert the three cogs in the bunker elevator."),
        ("Final boss: burst the spores", "Clear the vines with the Masamune, then use charged shots on the spores on his arms."),
    ])},
    {"id": "gk", "name": "Gorod Krovi", "steps": _steps([
        ("Turn on the power", "Switch is at ground level beside the incubator in Dragon Command."),
        ("Get the three Network Circuits",
         "Collect Code Cylinders, use the red, yellow and blue consoles, defend the pods about 30 seconds each, then insert the circuits in the Operations Bunker."),
        ("Ride the dragon to Pack-a-Punch", "Jump on a dragon from the upper platform in Dragon Command."),
        ("Get the Dragon Egg", "In the PaP bunker, get the Dragon Strike crystal, survive the lockdown, then shoot the egg above the sewer entrance."),
        ("Turn on the generator", "Kill a Valkyrie drone near the generator in the PaP building."),
        ("Solve the six valves",
         "Route air through the wall dials from the green start valve to the one holding the cylinder, avoiding loops. Put the cylinder in Sophia."),
        ("Place the egg in a dragon nest", "Nest is at spawn or outside Dragon Command; wait for a dragon to breathe fire."),
        ("Complete the egg challenges", "Tombstone at spawn: Napalm kills, penetrating multi-kills, melee kills."),
        ("Incubate the Dragon Egg", "Charge it in the PaP hatchery with kills, wait out the cooldown round, then pick it up."),
        ("Claim the Gauntlet of Siegfried", "Interact with the challenge tombstone while holding the egg."),
        ("Collect the six trophies", "Hidden around the map; they show up under the big monitor opposite Sophia."),
        ("Sophia password", "Shoot the letters on the cylinder panel to spell the password: KRONOS."),
        ("Complete the six Sophia trials",
         "Bombs, yellow orb, escort the Mangler, escort the Valkyrie, secure the pod (send the Whelp), then the key card. Start them with the green button."),
        ("Give Nikolai 1.0 the Power Core", "Pull the core from Sophia and send it off with the Gauntlet's dragon from spawn."),
        ("Fight Nikolai 1.0", "Stand on the red-star sewer gate in front of Sophia. Help kill the dragon, then take down the mech by its weak spots."),
    ])},
    {"id": "rev", "name": "Revelations", "steps": _steps([
        ("Override the four Corruption Engines", "Activate each for 500 points and hold out about 30 seconds until all four lights are green."),
        ("Collect the three Summoning Altar pieces", "Skull, purple gem and wheel are hidden across the classic-map areas."),
        ("Build a Summoning Altar", "Interact with any altar once you hold all the pieces."),
        ("Enter the Apothicon's mouth and unlock Pack-a-Punch",
         "In Nacht, hit the Apothicon with the electric trap via the control panel, go inside and shoot the yellow orb."),
        ("Upgrade the Apothicon Servant", "Hit five blue orbs around the maps with the Servant, then insert it into the changed Pack-a-Punch."),
        ("Shoot the gravestones",
         "One bullet each, in order: left side right grave, right side left grave, left side left grave, right side right grave."),
        ("Audio reel 1: protect the Keeper", "Summon the Keeper Protector, take him to the jump pad rock, and defend him for about two minutes."),
        ("Audio reel 2: Li'l Arnies", "Throw nine Li'l Arnies into the Apothicon's holes (three at a time), killing the Margwas between rounds of throws."),
        ("Collect the six body parts", "Shoot each stone, then pull the part out with the upgraded Servant."),
        ("Reveal Sophia and take reel 3", "Shoot the bones in Nacht with the Servant, then play the reel in Origins."),
        ("Reflect the turret beams", "Aim each Corruption Engine turret beam at a lighter crystal in the sky until you get kicked off."),
        ("Samantha's house and the Kronium book", "Use the Kino teleporter after Sophia charges it, grab the book from the bed, place it on the lectern."),
        ("Rune of Creation 1", "Get an egg, charge and hatch it in the acid pool pods, dig up a rune with the gateworm, stand in the Kino projector-room symbol."),
        ("Rune of Creation 2", "Repeat the egg, hatch, dig steps."),
        ("Rune of Creation 3", "Repeat the egg, hatch, dig steps."),
        ("Rune of Creation 4", "Repeat the egg, hatch, dig steps."),
        ("Boss room: book symbols", "Stand on the big symbol in the Kino projector room, read the book, then repeat the symbol order."),
        ("Survive the four phases", "Fire, shadow, crazy place, anti-gravity. Kill the Margwas quickly to earn the Summoning Key."),
        ("Throw the Summoning Key at seven spots",
         "Mob of the Dead poster, Origins gravestone, Shangri-La focusing stone, Verruckt fountain, Der Eisendrache clock, Nacht red barrel, Kino chandelier."),
        ("Shadow Man boss fight", "Charge the key with souls, throw it to Sophia so she can shoot him, repeat until he's in the Apothicon's mouth, then use the book."),
    ])},
]


DETAILS = {
    "soe": {
        0: ["Find the single Summoning Key and the four ritual items: the cop's badge, the toupee, the championship belt and the lawyer's pen.",
            "Each item starts the ritual in one of the four districts.",
            "At the end of every ritual a Gateworm drops. Pick it up - you need all four."],
        1: ["There is a convenient Rift portal in the Canal, at water level.",
            "In Beast Mode, break the door to reveal the Rift entrance. It turns from a yellow light into a red portal once activated.",
            "Inside, stand in front of the wall of yellow symbols so the bricks fall and reveal the Pack-a-Punch chamber.",
            "Put a Gateworm in each of the four fountains (wall-run to reach the two on the far side), then wait out the roughly 20 second countdown."],
        2: ["Ride the train and note the three unique red symbols you can see from the window.",
            "In the Rift, in Beast Mode, shoot the matching red symbols on the wall to open the sword chamber.",
            "Take the Egg from the throne and put it in each of the four octopus statues, killing zombies nearby to charge each one.",
            "Return the Egg to the sword chamber to get your sword. You can do this step before the rituals if you prefer."],
        3: ["Go to your own character's ritual area (the boxer uses the boxing ring, for example) and take the Arch-Ovum from the ghost.",
            "Place it on the floor in each of the four districts. A red circle appears and a Margwa boss spawns - kill it.",
            "After all four, go back to the ghost for the upgraded sword."],
        4: ["Visit Nero's library (Easy Street) and interact with the book on the floor so it floats. This spawns the flag in the subway.",
            "The flag sits in the middle of the subway room, reachable from a Rift or from the steps near the Junction.",
            "Plant it outside and defend it from zombies, rolling meatballs and flying enemies. If it takes too much damage you must redo the step.",
            "When it has fully glowed, give it to the ritual ghost. A purple beam hits the ghost and leaves a power-up."],
        8: ["Before you enter the Pack-a-Punch cave make sure you're happy with weapons and perks - the door blocks behind you.",
            "Survive a full round inside first.",
            "Interact with all four ghosts so they beam the Shadow Man and drop his bubble shield, then shoot him as he changes position.",
            "Repeat the cycle (Margwa and zombies, then the ghosts again) three or four times until he's captured and killed."],
        9: ["Zombies stop spawning and lots of Margwas spawn instead. White orbs appear - grab one quickly whenever your screen turns purple.",
            "Purple-eyed Margwas drop a purple fountain when killed that gives unlimited Beast Mode, but they can hurt you while you're in it.",
            "Make sure everyone knows how to enter Beast Mode before moving on."],
        10: ["Assign one player to each of the three train stations and one to the Junction.",
             "One person calls the train through the middle of the map to hit the giant Gateworm. That gives you about 20 seconds.",
             "Everyone enters Beast Mode. Station players shock their panels, then the Junction player shocks all three Gatekeepers.",
             "A red laser fires into the sky and the ending cutscene plays."],
    },
    "de": {
        6: ["Shoot the prongs on top of the teleporter with the un-upgraded bow until they turn gold.",
            "Sparking objects appear: the radio opposite Speed Cola, the clock above the command center, the clock in the hall from the clock tower, the phone on the pillar opposite the power switch, the globe in the small room beside Samantha's room, the box in the room above Double Tap, the car tyre by Double Tap and the phone at spawn near Quick Revive.",
            "Hit one blue spark after another, four times in quick succession, and don't let the round end."],
        7: ["Return to the teleporter. It should now be purple. Step in (with your team) to travel back in time.",
            "The screen goes black and white and Dr. Groph is putting something in the safe.",
            "Pick up the Soul Canister and the blue fuse in the room, and write down the code he enters."],
        8: ["Put the blue fuse in the Death Ray and flip the side switch to PROTECT.",
            "This powers the terminal by the Rocket and the one outside the Clock Tower.",
            "Enter Groph's code on the Clock Tower terminal (second level).",
            "The code changes every round, so don't kill the last zombie while you do this."],
        9: ["The safe near the teleporter opens. Take both tesla coils and the floppy disk.",
            "Put a coil in each tesla machine beside the Death Ray, set it to DESTROY and activate it to supercharge it.",
            "Then insert the floppy disk into the terminal."],
        12: ["Repeat the wisp step, then time travel again and take the keeper tablet from the 935 box in the corner.",
             "Take the golden rod from the crash wreckage to the knight's tomb and insert it under the tomb. A ghostly figure appears.",
             "Stand in each of the three ritual circles and kill nearby zombies to charge them, using the bow type that matches the screen tint.",
             "At the car site near Double Tap, also place the tablet."],
        13: ["At the pyramid, place the Soul Canister in each corner. The top half of the pyramid disappears.",
             "Each player stands a DG-4 on one of the pressure plates (solo: just one). That teleports you to the boss room.",
             "Run circles, avoid the fire and skulls, and recharge your DG-4. When he starts the lightning attack, plant a DG-4 under him and hide behind a pillar.",
             "When he's trapped his chest opens - switch to the bows and deal as much damage as you can. Repeat until he dies."],
    },
    "zns": {
        1: ["Take one black skull from the main ritual area to the altar elsewhere on the map that matches its symbol.",
            "Defend the skull there, then bring it back to the ritual altar.",
            "Repeat for all four skulls, then kill the Keepers and collect the new weapon underground."],
        2: ["Point your weapon at the pillar's symbols to read the challenges (for example kills underwater, watering plants, using traps).",
            "After \"Trial complete\", go back and accept the reward. The tiers give max ammo, a heavy weapon and an extra perk slot.",
            "Every player must complete their own. Lightning strikes the ritual site every few minutes - that matters for the shield."],
        10: ["Cog 1 (dragon room): use Anywhere But Here to reach the dragon room once the elevator room is revealed, and take the cog from the cracked shell.",
             "Cog 2 (docks): hit the zip line panel with the electric shield, ride the zip line, hold down and melee at the right moment to drop to a hidden platform.",
             "Cog 3 (plane): plant a seed, water it with blue water and shoot it with the KT-4 or Masamune for three rounds, then harvest the AA shell. Load the anti-aircraft gun and shoot the plane (Killing Time helps).",
             "Collect the smoldering cog from the wreckage, usually near Lab A or Lab B."],
        12: ["Use the Masamune to clear the vines blocking your way.",
             "Fight off the mobs until you have a clear shot at the boss's arms with the orange spores.",
             "Use charged Masamune shots to burst every spore."],
    },
    "gk": {
        1: ["With the power on, kill zombies to collect Code Cylinders.",
            "Insert the cylinders into the red, yellow and blue consoles and defend the pods for about 30 seconds each.",
            "Put the three Network Circuits into the machine inside the Operations Bunker."],
        3: ["In the Pack-a-Punch room go upstairs and take the crystal to get the Dragon Strike. The bunker locks down for a fight.",
            "After the lockdown, stand in front of the sewer entrance, look up and slightly left, and shoot the egg to knock it down.",
            "Pick the egg up."],
        5: ["Six valve machines: Dragon Command (upstairs), Infirmary (below Stamin-Up), Tank Factory (under the platform), Armory (by the bridge control), Department Store (opposite Kuda) and Supply Depot (under the bookcase).",
            "Blue light means air is flowing in, white means none, green marks the starting valve. Route air from the green valve to the one holding the cylinder without looping back.",
            "When it's right a new cylinder drops from the final machine. Put it in Sophia in Dragon Command."],
        10: ["Safe in the Operations Bunker: melee it with the Gauntlet of Siegfried.",
             "Puddle outside the Supply Depot (under Speed Cola): hit it with the Dragon Strike.",
             "Statue high above Dragon Command: shoot it, then take the trophy by the rubble on the right.",
             "Sewer from Pack-a-Punch: shoot the red light in the pipe, then take the trophy near the toilet by Pack-a-Punch.",
             "Supply Depot laser trap: use it and take the trophy from the floor.",
             "Pipe behind Double Tap (Tank Factory): hit it with the shield's dragon fire."],
        12: ["Trials start from the green button under the big monitor. Five come in random order and the sixth is always last.",
             "Defuse the bombs in the order shown on the monitor.",
             "Shoot the yellow orb again and again, following it as it moves.",
             "Escort the Russian Mangler, then the Valkyrie, back to Dragon Command without killing them (look for green eyes or lights).",
             "Secure the pod, then send the Whelp to fetch the cargo.",
             "Last: take the key card from the monitor, put it in the red-lit computer in the hatchery, defend against Manglers and return the card to Sophia."],
        14: ["Everyone stands on the red-star sewer gate in front of Sophia and drops into the final arena.",
             "Help Nikolai's mech kill the mother dragon.",
             "Then he turns on you: run the perimeter, use the corner trenches, and aim for his yellow weak spots."],
    },
    "rev": {
        1: ["Skull: in Verruckt (downstairs bookshelf by Speed Cola, or upstairs on a wheelchair).",
            "Purple gem: Shangri-La temple wall near the primis statues, next to the Der Eisendrache pyramid, or on a table in Kino's outdoor room.",
            "Wheel / flag: Mob of the Dead cafeteria wall, or near the Origins altar and excavation site hub."],
        4: ["Hit five small blue orbs around the maps with the Apothicon Servant: Verruckt (end of the upstairs by the jump pad, and by the Corruption Engine), Nacht (upper floor outside left of the wall weapon), Mob of the Dead (above the Corruption Engine) and Shangri-La (above the temple).",
            "Each orb is sucked to the Pack-a-Punch machine. Once all five are in, insert the Servant for the upgraded version."],
        5: ["The graves are at spawn: two are visible from the enclosure by Quick Revive, the other two from the left of the Origins jump pad.",
            "Use only one bullet per shot. This can be done from round 1."],
        8: ["Shangri-La: in front of Stamin-Up, look up and shoot the cracked stone.",
            "Origins: outside the map to the right of the Keeper Protector panel, in the fire by the giant's footprint.",
            "Verruckt: outside the map, left of the Corruption Engine by the waterfall.",
            "Spawn: left of the Corruption Engine facing the church, among the floating rocks.",
            "Nacht (upper floor): turn left from the wall gun toward the red beam and shoot the underside of the stone ceiling.",
            "Der Eisendrache: bottom-right corner of the third wall-run wall. Shoot with the Pack-a-Punched weapon, then suck it up with the Servant."],
        11: ["After Sophia is solid, she floats to the theatre. Let her recharge the Kino teleporter and take it to the house.",
             "Grab the Kronium book from the bed in the small bedroom, then place it on the lectern on the Kino stage."],
        12: ["Eggs are yellow and sit in many spots (for example the Der Eisendrache primis statue room, the Origins dig site, near the fire in Shangri-La, above Speed Cola in Verruckt, opposite the Kino stage).",
             "Put the egg in a pod around the Apothicon's acid pool and kill 15 or more enemies inside the pool to hatch the Gateworm.",
             "Walk slowly around each main area until you hear a sonar beep that gets louder, then interact at full intensity to dig up the rune.",
             "Stand inside the glowing symbol in the Kino projector room."],
        16: ["Stand on the large purple symbol in the upstairs Kino projector room to be teleported to the boss room.",
             "Go to the opposite side and read the book, memorise the symbol order, then interact with the symbols in that order as they appear."],
        17: ["Phase 1 (fire): the floor burns and lava appears - stay out of it.",
             "Phase 2 (shadow): lightning rains down - keep moving, preferably on the upper levels.",
             "Phase 3 (crazy place): walls appear - avoid getting trapped.",
             "Phase 4 (anti-gravity): stay inside the spotlights.",
             "The Summoning Key drops from the sky when you finish."],
        18: ["Mob of the Dead: stand on the foot bridge facing away from the Corruption Engine and throw it at the poster in the cell slightly to the left.",
             "Origins: the gravestone at the top of the dig site, far corner on a wooden platform.",
             "Shangri-La: the focusing stone (pole with a blue ball) at the top of the stairs on the left.",
             "Verruckt: the water fountain to the left of the Corruption Engine.",
             "Der Eisendrache: the clock above and opposite the Corruption Engine.",
             "Nacht: the red barrel outside the window between the spawn and Der Eisendrache entrances.",
             "Kino: the huge chandelier. Then go back through the Kino teleporter for the real fight."],
        19: ["Place the key on a green light and kill zombies nearby to charge it.",
             "Pick it up and throw it to Sophia so she lasers the Shadow Man. Shoot him yourself as well.",
             "Repeat until he's inside the Apothicon's mouth, then interact with the book."],
    },
}

for _m in DEFAULT_MAPS:
    for _i, _ds in DETAILS.get(_m["id"], {}).items():
        _m["steps"][_i]["details"] = _ds

DEFAULT_SETTINGS = {
    "topmost": False, "mode": "run", "hide_done": False,
    "overlay": False, "overlay_alpha": 0.85, "overlay_scale": 1.0, "overlay_click": False,
    "overlay_auto": True, "overlay_x": 40, "overlay_y": 40, "game_exe": "BlackOps3.exe",
}


DEFAULT_SETTINGS.update({
    "theme": "Blood Red", "accent": None, "timer_font": "Consolas", "timer_scale": 1.0,
    "show_cs": True, "show_notes": True, "show_images": True, "confirm_reset": True,
    "hk_main": "F6", "hk_undo": "F7", "hk_pause": "F8", "hk_overlay": "F9",
    "overlay_show_map": True, "overlay_show_tag": True, "overlay_show_next": True,
    "overlay_show_timers": True, "seen_maps": None,
})


def _attach_default_details(maps):
    # older saved maps get the new detail bullets (only if the step name still matches)
    for m in maps:
        base = next((b for b in DEFAULT_MAPS if b["id"] == m.get("id")), None)
        if not base:
            continue
        for i, s in enumerate(m["steps"]):
            if i < len(base["steps"]) and "details" not in s \
                    and base["steps"][i]["name"] == s["name"] and base["steps"][i].get("details"):
                s["details"] = list(base["steps"][i]["details"])


# ---------------------------------------------------------------- Zombies Chronicles + Origins
CHRON_MAPS = [
    {"id": "moon", "name": "Moon", "steps": _steps([
        ("Play Simon Says", "At the four terminals outside, watch the flashing colours and press the terminals to match, using the colour key."),
        ("Hack the terminals", "Get the Hacking Device, hack the four wall panels on floor 2 (500 points), then quickly hack the four green terminals and press the four black wall buttons."),
        ("Wait for the Excavator to breach Tunnel 6", "Listen for the announcer saying active decompression in tunnel 6, then use the Hacking Device on the control-room terminal to shut the excavator off."),
        ("Move the Focusing Stone", "Melee or shoot the stone in tunnel 6 along its route until it rests at the base of the pyramid. A canister rises."),
        ("Fill the pyramid canister", "Kill about 25 zombies close to the pyramid."),
        ("Flip the switch to release Samantha", "Switch is on the wall opposite the canister. This ends the first half (Cryogenic Slumber Party), which can be done solo."),
        ("Get the plates", "Drop the plates behind Speed Cola, suck them in with the Gersch Device, then carry them to the computer with the Quantum Entanglement Device."),
        ("Find the wire", "A wire lies near the rooms where you found the Hacking Device. Pick it up."),
        ("Connect the wire and place the Golden Rod", "At the Quick Revive room computer, place the wire, then Richtofen puts the Golden Rod between the plates."),
        ("Power the device", "Richtofen mashes interact in front of the device while Maxis and Richtofen talk, then takes the rod at the end."),
        ("Fill the four pyramid canisters", "Interact with the pyramid's circular base, then kill about 25 zombies per canister."),
        ("Place the Golden Rod in the pyramid", "Put the rod into the circular relic base and let the dialogue play."),
        ("Play Simon Says (three rounds)", "Same terminals as step 1 but harder. Write the sequence down if you need to."),
        ("Get the Focusing Stone from the pyramid", "Throw the Quantum Entanglement Device at the pyramid's circular base."),
        ("Launch the rockets", "Throw the Gersch Device at the stone resting at the end of the yellow railings by the Simon Says area."),
    ])},
    {"id": "sl", "name": "Shangri-La", "steps": _steps([
        ("Turn on power and open Pack-a-Punch", "Flip the water-wheel switch for power, then have players stand on the pressure plates beside the rotating statues."),
        ("Start the eclipse", "All four players press the skull buttons around Quick Revive at the same moment. You'll repeat this before many later steps."),
        ("Match the slab symbols", "Press the skull button near the minecart tunnel, then pairs of players stand on slabs with matching symbols."),
        ("Use the water slide", "Three players stand on the metal grates at the bottom while the fourth rides down the slide to free the explorers."),
        ("Shrink the stone and ride it to the temple", "Shoot down the grey ball, shrink it with the shrink ray, push it down the slide, then fly up the water chute."),
        ("Turn the gas valve", "Start an eclipse and turn the valve wheel in the minecart-side tunnel until the explorer speaks."),
        ("Light the gas pipes with a Napalm zombie", "Lure a Napalm zombie past all four gas leaks, then pull the lever beside the valve."),
        ("Plug the waterfall holes with trip mines", "Place trip mines opposite the four holes, lure zombies into them, then press the button at the bottom of the waterfall."),
        ("Melee the 12 stone symbols", "Start an eclipse, then melee all 12 wall stones in any order."),
        ("Explode the wooden trap", "Blow up the wooden trap outside the map beside the hut on the minecart side."),
        ("Rotate the dials to 16, 1, 3, 4", "Start an eclipse and set the four golden dials in the mud room to those numbers."),
        ("Hit the gongs and shoot the orbs for dynamite", "Melee the four gongs that ring in the right sequence, shoot the orbs with the upgraded shrink ray, then shoot the sixth orb at the meteor."),
        ("Free the explorers", "Climb the temple stairs with Pack-a-Punch activated and give the explorers the dynamite."),
        ("Claim the Focusing Stone", "Re-activate Pack-a-Punch, return to the top of the stairs and take the shrunken meteor. You keep all perks as a reward."),
    ])},
    {"id": "asc", "name": "Ascension", "steps": _steps([
        ("Throw the Gersch Device at the generator", "Throw it out of the map by the launch pad corridor so it sucks in the generator."),
        ("Node 1: change the TV to a triangle", "Interact with the static TV by the lander pad where claymores are sold."),
        ("Node 2: press the four perk buttons together", "During a monkey round, all four players press their buttons in the perk rooms at the same moment."),
        ("Node 3: open Pack-a-Punch and use the launch bay clock", "Move the three landers, open the launch doors from the control panel, then stand in the circle in front of the clock."),
        ("Node 4: ride the landers to spell LUNAR", "Ride the landers to Stamin-Up, back to spawn, to Speed Cola, then back to Stamin-Up."),
        ("Power the Casimir Mechanism", "Throw the Gersch Device on the floor orb, then the Matryoshka Dolls, then hit the black hole with maximum firepower."),
    ])},
    {"id": "origins", "name": "Origins", "steps": _steps([
        ("Fire Staff: build it", "Needs 3 red parts, the red disc, the black disc and the gramophone. Build it at the excavation site tombs."),
        ("Fire Staff: solve the riddle", "In the Crazy Place, kill zombies with the Fire Staff while standing on the fire pad until all four fire pits are full."),
        ("Fire Staff: upgrade it", "Read the church symbols, shoot the numbered torches, line up the red rings, then charge the staff at the red pedestal."),
        ("Ice Staff: build it", "Needs 3 blue parts, the blue disc, the black disc and the gramophone. Build it at the excavation site tombs."),
        ("Ice Staff: solve the riddle", "In the Crazy Place, hit the blue-symbol tablets in the right order. A mistake resets the sequence."),
        ("Ice Staff: upgrade it", "Freeze and smash the three weeping tombstones, line up the blue rings, then charge the staff at the blue pedestal."),
        ("Wind Staff: build it", "Needs 3 yellow parts, the yellow disc, the black disc and the gramophone. Build it at the excavation site tombs."),
        ("Wind Staff: solve the riddle", "In the Crazy Place, line the circles up to match the solution picture. They shift up one at a time when right."),
        ("Wind Staff: upgrade it", "Blow the smoke from three stone balls toward the excavation site, line up the yellow rings, then charge the staff at the yellow pedestal."),
        ("Lightning Staff: build it", "Needs 3 purple parts, the purple disc, the black disc and the gramophone. Build it at the excavation site tombs."),
        ("Lightning Staff: solve the riddle", "In the Crazy Place, shoot the purple triangles in the right order: 1-3-6, then 3-5-7, then 2-4-6."),
        ("Lightning Staff: upgrade it", "Stop the sparks on every sparking panel, line up the purple rings, then charge the staff at the purple pedestal."),
        ("Prepare the Maxis Drone, Tactical Airstrike and Thunderfists", "Do the upfront buildables and the Ritual challenges before the robot steps."),
        ("Place the staffs in the robots and the Fire Staff stand", "Three staffs go inside the robots, the Fire Staff goes on its new stand."),
        ("Press the red buttons in the robot heads", "Pick the staffs back up first. Then enter each robot head and press the red button."),
        ("Rain Fire: throw the Tactical Airstrike", "Do this right after pressing the red button. Throw the marker next to the concrete circle by the downed plane."),
        ("Unleash the Horde: scavenge the hole with the Maxis Drone", "Use the drone on the new hole, then kill the 3-6 Panzer soldiers that appear."),
        ("Skewer the winged beast: shoot the plane, kill the white zombie", "Needs the upgraded Ballista and two Zombie Blood drops. Take the Maxis Drone the white zombie drops."),
        ("Wield a fist of iron: upgrade the Thunderfists", "Punch the white-handed zombies on the lower excavation level until a drop appears, then use the upgraded fists."),
        ("Place all four staffs on the altars", "Back in the Crazy Place, put each staff on the altar where its crystal was. This unlocks the Little Lost Girl achievement."),
        ("Open the rift and take the teleporter", "Punch zombies with the Thunderfists to open the seal, release the Maxis Drone, then step on the new teleporter for the cutscene."),
    ])},
    {"id": "kino", "name": "Kino der Toten (song)", "steps": _steps([
        ("Meteorite 1: spawn room", "Inside the glass casing in the spawn room."),
        ("Meteorite 2: above the stage", "In the chamber above the stage, on a shelf."),
        ("Meteorite 3: above the stage", "In the same chamber, on a table. Activating all three plays the 115 song."),
    ])},
    {"id": "verr", "name": "Verruckt (side eggs)", "steps": _steps([
        ("Shoot the fountain statue", "Hit the statue in the middle of the map with an explosive weapon and it vomits blood."),
        ("Find Dr. Monty's radio", "The new radio is near the Juggernog machine."),
    ])},
    {"id": "sn", "name": "Shi No Numa (side egg)", "steps": _steps([
        ("Find Dr. Monty's radio", "The new radio is right at the spawn area."),
    ])},
]
DEFAULT_MAPS.extend(CHRON_MAPS)

CHRON_DETAILS = {
    "moon": {
        0: ["Terminal colour key from left to right: red, green, blue, yellow.",
            "Example: if the screen flashes red, green, yellow, blue you press 1, 2, 4, 3.",
            "All the screens flash green when you get it right."],
        1: ["The Hacking Device sits on a shelf, barrel or desk in one of the small lab rooms on the three floors. A yellow leaflet gives hints.",
            "Hacking the wall panels (second floor) starts a countdown, so be quick finding the four green terminals.",
            "Then press the four black buttons on the same wall. The lights stay on if it worked."],
        3: ["You need the Wave Gun from the box, lots of points for doors, and you'll swap between the PES and the Hacking Device.",
            "If the stone gets stuck on a satellite dish above, shoot it down with the Wave Gun and keep following it."],
        6: ["Stand to the right of Speed Cola at Area 51, look outside the map and throw a grenade or semtex to drop the plates.",
            "Suck the parts in with the Gersch Device and take them to the teleporter.",
            "On the moon base the parts are on the floor to the left of Quick Revive. Pick them up with the Quantum Entanglement Device and deliver them to the computer in the same room.",
            "A special drop appears in front of Quick Revive when it works."],
        7: ["It's usually on the floor near cabinets, stairwells or squeezed against other objects.",
            "There's one spot outside near Mule Kick."],
        12: ["You must play three games of Simon Says this time.",
             "Use the same colour key as before."],
    },
    "sl": {
        0: ["Pressure plates: middle of spawn, next to the gong in the first waterfall area, in front of the power switch below, and the mine tunnel leading away from the power room."],
        2: ["The player who pressed the button calls out each symbol; a teammate finds the matching slab; both step on at the same time.",
            "There are 12 slabs near the tunnel entrance and another 12 on the other side of the map."],
        4: ["The shrink ray is a box weapon in this version.",
            "Push the shrunk ball into the water slide, jump in after it, then stand on the circular water chute to fly back up."],
        6: ["Turn the valve wheel several times until the explorer says he will thank you in person."],
        7: ["Two pipes are in the tunnel and another two near the perk room and weapon box.",
            "A small flame appears from each pipe you light."],
        8: ["Trip mines (Spikemores in the original) can be bought from the wall.",
            "Place them at four spots opposite the circular holes in the tunnel wall, near the flame torches."],
        9: ["Locations: 5 in spawn, 2 mud room side, 2 minecart side, 3 down below."],
        10: ["The numbers are always the same, so you don't need the radio.",
             "Symbol key: C = 10, I = 5, dot = 1. So 16 is C, I and one dot."],
        11: ["There are 8 gongs. Four ring continuously and you must hit exactly those four in sequence. A wrong gong stops the tone and turns the orbs red.",
             "While the fourth gong is still ringing, shoot the orbs on top of the Golden Rods with the upgraded shrink ray to get the dynamite."],
    },
    "asc": {
        1: ["Spin the box to get the Gersch Device (usually the top of the control tower).",
            "Throw it outside the map boundary from the left of the launch platform ramp. Do it wrong and Samantha steals it."],
        2: ["Buttons are in the same rooms as Jugger-Nog, PhD (Widow's Wine), Speed Cola and Stamin-Up. There is no button for Quick Revive."],
        3: ["Ride each of the three lunar landers to its alternate position, then use the control panel on the third floor rooftop to open the launch doors.",
            "This step takes a while and needs someone to hold a crawler outside for several minutes.",
            "In Zombies Chronicles you don't need the ballistic knife or to stand still, but you must stay within the circle in front of the clock."],
        5: ["Recommended firepower: Zeus Cannon (upgraded Thundergun), upgraded crossbow and two upgraded Rayguns, or equally strong Black Ops 3 weapons.",
            "This is a 4-player Easter egg and can't be done solo."],
    },
    "origins": {
        0: ["Parts have no fixed spawn spots but each has specific requirements.",
            "Use the gramophone at the excavation site to open the portal to the chambers where the staff crystals are.",
            "Build each staff on the lower level of the excavation site, at its tomb."],
        1: ["Every staff has a riddle written near its teleporter in the chamber (the Crazy Place).",
            "The Fire one isn't really a riddle: stand on the fire pad (flames coming through the floor) and kill zombies with the Fire Staff until all four fire pits are full of souls."],
        2: ["Go to the church. On the second floor, red dots on the wall mark glowing symbols. Read the symbols from left to right to get four numbers.",
            "Shoot the matching numbered torches at ground level near the tank with the Fire Staff. There is no number 4 torch - it's always the torch above the blood mark.",
            "The symbols on the wall are usually among 11, 5, 9, 7, 6, 3 and 4 (not every number is needed).",
            "On the lower level of the excavation site a red ball floats under four rings. Use the switches around that level (each changes one ring's colour) until every ring is red, then shoot the ball.",
            "Go back to the chamber, put the staff where you collected the crystal and kill zombies close by to fill it with souls. It stays locked until it's full, so keep a good second weapon.",
            "Pick up the upgraded staff."],
        3: ["Same build rules as every staff: 3 parts, the matching disc, the black disc and the gramophone."],
        4: ["The Ice riddle is the easiest to crack by trial and error: hit each tablet with a blue symbol. A mistake resets the whole sequence and there are only 5 or 6 tablets."],
        5: ["Find the three tombstones that have water trickling down them. Shoot each one with the Ice Staff to freeze it, then smash it with a normal weapon.",
            "Locations: right of the MP-40 wall buy near Generator 4; from the giant robot's right footprint on the Generator 4 side, next to the mud pool by the soul box; and on a small mound outside the map behind the bunker by Generator 2, next to the downed robot's hand.",
            "On the lower level of the excavation site, line up the blue rings with the switches, then shoot the blue orb.",
            "Put the staff in the chamber where you picked up the crystal and kill zombies nearby to charge it, then pick it up."],
        6: ["The walking robots make the Wind parts the hardest to get, so expect a wait."],
        7: ["Line the circles up to match the solution picture. If they're right they shift upwards one at a time.",
            "A good tip: add your own screenshot of the solution to this step with the Add picture button."],
        8: ["Use the Wind Staff to turn the smoke from the stone balls so it points at the excavation site.",
            "Locations: Generator 6 (Jug) near the map boundary, facing the excavation site; Generator 5, standing right beside Stamin-Up; and no-man's land (through the church basement and out the other side, on your right).",
            "On the lower level of the excavation site, line the dials up yellow and shoot the yellow orb.",
            "Put the staff in the chamber where you got the crystal and kill zombies nearby to charge it, then pick it up."],
        9: ["The Lightning tunnel entrance is near Generator 5.",
            "This staff needs 3 purple parts, the purple disc, the black disc and the gramophone."],
        10: ["The riddle is about musical notes written on the stone blocks near the Lightning teleporter. Play the purple triangles in the right order.",
             "Shoot 1, 3, 6 first. Then 3, 5, 7. Then 2, 4, 6."],
        11: ["Find each sparking panel and turn its dial to stop the sparks. Only one panel sparks at a time. There are about 8.",
             "Locations: beside Stamin-Up; the wall behind the church stairs (ground level); left of the Mystery Box spot in the church; Generator 4, right of the Wind tunnel entrance; the wall leading from the bottom floor of spawn to the upper level; left of the back exit of the bunker beside Generator 2; the rock wall on the outside of the Excavation Zone, across from the path from the trenches to the church.",
             "On the lower level of the excavation site, line the dials up purple with the switch machines, then shoot the purple orb. It slowly rises.",
             "Put the staff in the chamber where you got the purple crystal and kill 20-30 zombies nearby, then pick it up."],
        12: ["There's a lot of upfront work: the Maxis Drone and the Tactical Airstrike buildables, plus getting the Thunderfists from the Ritual challenges.",
             "Tactical Airstrike: carry the four stone tablets from the Tank Room to the church fountain, kill enough zombies to clean them, then carry them back to the Tank Room without stepping in any mud.",
             "Put the tablets on the bench near the door that leads to the mud. When all four are placed you get the Tactical Airstrike, which you throw like a monkey bomb.",
             "All four staffs must be fully upgraded before you continue."],
        13: ["Once all the Ultimate staffs are done, put the Wind, Ice and Lightning staffs inside the robots. Only one type per robot.",
             "You get inside a robot the same way you did for the Wind parts.",
             "Then go to the lower level of the excavation site where a new stand has appeared and put the Fire Staff on it."],
        14: ["The staffs you placed in the robots reappear at the excavation site. Strongly recommended: pick them back up.",
             "The robots now walk in sync. Enter a robot's head and press the red button on the control panel."],
        15: ["Do this immediately after pressing the red button - don't wait.",
             "Find the downed plane off the map near Stamin-Up and throw the Tactical Airstrike marker so it lands near the concrete circle.",
             "The robot's bomb blows the hole open."],
        16: ["Use the Maxis Drone to scavenge the new hole. That triggers between 3 and 6 Panzer soldiers.",
             "Kill them all."],
        17: ["You need the upgraded Ballista.",
             "Pick up a Zombie Blood drop, go to the excavation site and shoot the burning plane down, as you did for the Fire Staff part.",
             "Grab another Zombie Blood, find the odd-coloured (whitish) zombie in the excavation site and kill it. It drops a Maxis Drone - pick it up."],
        18: ["On the lower level of the excavation site you'll see hordes of zombies with white hands. Punch them repeatedly until a new drop appears and pick it up.",
             "You now have upgraded Thunderfists, which stay strong even past round 20. Punch zombies again until they stop coming."],
        19: ["Go back to the Crazy Place and put all four staffs on the altars where the crystals used to be.",
             "That earns the Little Lost Girl achievement - but stay, the final cutscene is close."],
        20: ["Use the Thunderfists to punch the knight zombies in the area. Every kill sends a soul up to the ceiling and opens the hole in the seal. You only need about as many as it takes to fill a soul box.",
             "This is hard because you've given up your best weapon. Stay out of corners and keep using the melee attack.",
             "When the hole is big enough, release a Maxis Drone so it flies up through the rift.",
             "A bright light lands on the floor - a new teleporter. Step on it and hold interact. The game ends abruptly, then the final cutscene plays."],
    },
    "kino": {0: ["Activating all three orange meteorites plays the 115 song."]},
    "verr": {1: ["Dr. Monty talks about Verruckt being an asylum."]},
}
for _m in CHRON_MAPS:
    for _i, _ds in CHRON_DETAILS.get(_m["id"], {}).items():
        _m["steps"][_i]["details"] = _ds

ART = {
    "moon": {0: ["moon_simon"], 12: ["moon_simon"]},
    "sl": {10: ["sl_dials"]},
    "asc": {4: ["asc_lander"]},
    "origins": {2: ["staff_fire"], 5: ["staff_ice"], 8: ["staff_wind"], 10: ["lightning_riddle"], 11: ["staff_lightning"]},
}
for _m in DEFAULT_MAPS:
    for _i, _keys in ART.get(_m["id"], {}).items():
        _m["steps"][_i]["art"] = list(_keys)

OLD_ORIGINS = ["Fire Staff: build it", "Fire Staff: upgrade it", "Ice Staff: build it", "Ice Staff: upgrade it",
               "Wind Staff: build it", "Wind Staff: upgrade it", "Lightning Staff: build it", "Lightning Staff: upgrade it"]


def _upgrade_origins(maps):
    # swaps the old staffs-only Origins for the full easter egg (keeps your pictures and PBs by step name)
    base = next(b for b in DEFAULT_MAPS if b["id"] == "origins")
    for m in maps:
        if m.get("id") == "origins" and [s["name"] for s in m["steps"]] == OLD_ORIGINS:
            pics = {s["name"]: s["pics"] for s in m["steps"] if s.get("pics")}
            m["name"] = base["name"]
            m["steps"] = json.loads(json.dumps(base["steps"]))
            for st in m["steps"]:
                if st["name"] in pics:
                    st["pics"] = pics[st["name"]]


GROUP_ORDER = ["Black Ops 3", "Chronicles", "Custom"]
GROUP_OF = {**{k: "Black Ops 3" for k in ("soe", "de", "zns", "gk", "rev")},
            **{m["id"]: "Chronicles" for m in CHRON_MAPS}}


def _attach_default_details(maps):
    # saved maps pick up new detail bullets and pictures (only if the step name still matches)
    for m in maps:
        base = next((b for b in DEFAULT_MAPS if b["id"] == m.get("id")), None)
        if not base:
            continue
        for i, s in enumerate(m["steps"]):
            if i < len(base["steps"]) and base["steps"][i]["name"] == s["name"]:
                for key in ("details", "art"):
                    if key not in s and base["steps"][i].get(key):
                        s[key] = list(base["steps"][i][key])


# ---------------------------------------------------------------- helpers
def fmt(t):
    """Seconds -> M:SS.cc (or H:MM:SS.cc); no hundredths if turned off in Settings."""
    if not SHOW_CS:
        whole = int(abs(t))
        h, rem = divmod(whole, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    cs = int(round(abs(t) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{c:02d}"
    return f"{m}:{s:02d}.{c:02d}"


def fmt_delta(d):
    sign = "+" if d >= 0 else "-"
    a = abs(d)
    return f"{sign}{a:.2f}" if a < 60 else sign + fmt(a)


_NUM = re.compile(r"^\s*(?:[-*\u2022]\s+|(?:step\s*)?\d+\s*[.):\-\u2013]\s*)", re.I)
_SUB = re.compile(r"^[-*\u2022+]\s*")


def parse_steps(text):
    # One step per line, optional note after ' | '.
    # Indented lines that start with - or a bullet become details of the step above.
    # Leading numbering like '1.' or 'Step 3 -' is removed.
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if raw[:1] in (" ", "\t") and _SUB.match(line) and out:
            d = _SUB.sub("", line, count=1).strip()
            if d:
                out[-1].setdefault("details", []).append(d)
            continue
        line = _NUM.sub("", line, count=1).strip()
        if not line:
            continue
        name, _, note = line.partition("|")
        name = name.strip()
        if name:
            out.append({"name": name, "note": note.strip()})
    return out


def steps_to_text(steps):
    lines = []
    for s in steps:
        lines.append(s["name"] + (f" | {s['note']}" if s.get("note") else ""))
        for d in s.get("details", []):
            lines.append(f"    - {d}")
    return "\n".join(lines)


class Run:
    """Pure stopwatch + checklist logic (no GUI)."""

    def __init__(self, n, clock=time.perf_counter):
        self.n, self.clock = n, clock
        self.state = "idle"  # idle | running | paused | finished
        self.base = 0.0
        self.t0 = None
        self.done = {}       # step index -> {"split": cumulative secs, "dur": secs for that step}
        self.order = []
        self.last_event = 0.0

    def elapsed(self):
        if self.state == "running":
            return self.base + (self.clock() - self.t0)
        return self.base

    def step_elapsed(self):
        return max(0.0, self.elapsed() - self.last_event)

    def start(self):
        if self.state == "idle":
            self.state, self.t0 = "running", self.clock()

    def pause(self):
        if self.state == "running":
            self.base = self.elapsed()
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self.t0 = self.clock()
            self.state = "running"

    def complete(self, i):
        if self.state != "running" or i in self.done or not 0 <= i < self.n:
            return False
        e = self.elapsed()
        self.done[i] = {"split": e, "dur": e - self.last_event}
        self.order.append(i)
        self.last_event = e
        return True

    def undo(self):
        if self.state not in ("running", "paused") or not self.order:
            return False
        del self.done[self.order.pop()]
        self.last_event = self.done[self.order[-1]]["split"] if self.order else 0.0
        return True

    def next_index(self):
        for i in range(self.n):
            if i not in self.done:
                return i
        return None

    def all_done(self):
        return len(self.done) == self.n

    def finish(self):
        if self.state in ("running", "paused"):
            self.base = self.elapsed()
            self.state = "finished"
        return self.base

    def to_dict(self):
        return {"base": self.elapsed(), "last_event": self.last_event, "order": list(self.order),
                "done": {str(k): v for k, v in self.done.items()}}

    @classmethod
    def from_dict(cls, n, d, clock=time.perf_counter):
        r = cls(n, clock)
        r.state = "paused"
        r.base = float(d.get("base", 0))
        r.done = {int(k): v for k, v in d.get("done", {}).items() if int(k) < n}
        r.order = [i for i in d.get("order", []) if i in r.done]
        r.last_event = float(d.get("last_event", 0))
        return r


def new_record():
    return {"pb_total": None, "pb_splits": {}, "best_seg": {}, "runs": []}


def store_run(rec, steps, run, finished, now=None):
    """Save a run into a map's record. Returns (is_pb, gold_count, previous_pb)."""
    names = [s["name"] for s in steps]
    total = run.base
    prev_pb = rec["pb_total"]
    gold = 0
    for i, d in run.done.items():
        nm = names[i]
        best = rec["best_seg"].get(nm)
        if best is None or d["dur"] < best:
            if best is not None:
                gold += 1
            rec["best_seg"][nm] = d["dur"]
    is_pb = False
    if finished and (prev_pb is None or total < prev_pb):
        rec["pb_total"] = total
        rec["pb_splits"] = {names[i]: d["split"] for i, d in run.done.items()}
        is_pb = True
    rec["runs"].append({
        "date": (now or dt.datetime.now()).isoformat(timespec="seconds"),
        "total": total, "finished": finished,
        "splits": {names[i]: d for i, d in run.done.items()},
    })
    del rec["runs"][:-200]
    return is_pb, gold, prev_pb


# ---------------------------------------------------------------- data
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "BO3EETracker")
DATA_FILE = os.path.join(DATA_DIR, "data.json")


def default_data():
    return {"maps": json.loads(json.dumps(DEFAULT_MAPS)), "records": {},
            "settings": dict(DEFAULT_SETTINGS), "active": None, "guide": {}}


def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("maps"), list):
            raise ValueError
    except (OSError, ValueError):
        d = default_data()
    d.setdefault("records", {})
    s = d.setdefault("settings", {})
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    d.setdefault("active", None)
    d.setdefault("guide", {})
    seen = set(s.get("seen_maps") or ["soe", "de", "zns", "gk", "rev"])
    have = {m["id"] for m in d["maps"]}
    for dm in DEFAULT_MAPS:
        if dm["id"] not in seen and dm["id"] not in have:
            d["maps"].append(json.loads(json.dumps(dm)))
    s["seen_maps"] = sorted(seen | {dm["id"] for dm in DEFAULT_MAPS})
    _attach_default_details(d["maps"])
    _upgrade_origins(d["maps"])
    return d


def save_data(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        messagebox.showerror("Couldn't save", str(e))


# ---------------------------------------------------------------- global hotkeys (Windows)
def start_global_hotkeys(q, vks=None):
    """F6 / F7 / F8 even when the game has focus. Returns True if registered."""
    if os.name != "nt":
        return False
    ready, result = threading.Event(), [False]

    def worker():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            got = 0
            for hid, vk in (vks or ((1, 0x75), (2, 0x76), (3, 0x77), (4, 0x78))):
                if user32.RegisterHotKey(None, hid, 0x4000, vk):  # MOD_NOREPEAT
                    got += 1
            result[0] = got > 0
            ready.set()
            if not got:
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    q.put(int(msg.wParam))
        except Exception:
            pass
        finally:
            ready.set()

    threading.Thread(target=worker, daemon=True).start()
    ready.wait(1.5)
    return result[0]


# ---------------------------------------------------------------- widgets
class Flat(tk.Label):
    def __init__(self, parent, text, command, bg, fg=None, hover=None,
                 font=(FONT, 10, "bold"), padx=16, pady=8, anchor="center"):
        super().__init__(parent, text=hk(text), bg=bg, fg=fg or C["text"], font=font,
                         padx=padx, pady=pady, cursor="hand2", anchor=anchor)
        self._bg, self._hover = bg, hover or bg
        self.bind("<Enter>", lambda e: self.config(bg=self._hover))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg))
        self.bind("<Button-1>", lambda e: command())

    def set_colors(self, bg, hover=None, fg=None):
        self._bg, self._hover = bg, hover or bg
        self.config(bg=bg)
        if fg:
            self.config(fg=fg)


class Scroll(tk.Frame):
    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")


def card(parent, bg=None, pad=14, border=None):
    return tk.Frame(parent, bg=bg or C["card"], highlightthickness=1,
                    highlightbackground=border or C["border"], padx=pad, pady=pad)


class Dialog(tk.Toplevel):
    def __init__(self, parent, title, width=640, height=560):
        super().__init__(parent, bg=C["bg"])
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        self.body = tk.Frame(self, bg=C["bg"], padx=24, pady=20)
        self.body.pack(fill="both", expand=True)
        self.grab_set()

    def label(self, text):
        tk.Label(self.body, text=text, bg=C["bg"], fg=C["muted"],
                 font=(FONT, 9, "bold")).pack(anchor="w", pady=(10, 3))


def style_entry(parent, var, **kw):
    return tk.Entry(parent, textvariable=var, bg=C["card2"], fg=C["text"],
                    insertbackground=C["text"], relief="flat", highlightthickness=1,
                    highlightbackground=C["border"], highlightcolor=C["green"],
                    font=(FONT, 11), **kw)


def map_dialog(app, m=None):
    d = Dialog(app, "Edit map" if m else "Add a map", height=620)
    d.label("MAP NAME")
    name = tk.StringVar(value=m["name"] if m else "")
    e = style_entry(d.body, name)
    e.pack(fill="x", ipady=5)
    e.focus_set()
    d.label("STEPS  (one per line, optional note after a  |  )")
    tk.Label(d.body, text="You can paste a whole list from a guide - numbering like \"1.\" or \"Step 3 -\" is removed for you.",
             bg=C["bg"], fg=C["muted"], font=(FONT, 9), wraplength=580, justify="left").pack(anchor="w")
    box = tk.Frame(d.body, bg=C["bg"])
    box.pack(fill="both", expand=True, pady=(6, 0))
    txt = tk.Text(box, bg=C["card2"], fg=C["text"], insertbackground=C["text"], relief="flat",
                  wrap="word", font=(FONT, 10), highlightthickness=1,
                  highlightbackground=C["border"], highlightcolor=C["green"], padx=8, pady=8)
    sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    if m:
        txt.insert("1.0", steps_to_text(m["steps"]))

    def save():
        nm = name.get().strip()
        steps = parse_steps(txt.get("1.0", "end"))
        if not nm:
            messagebox.showwarning("Missing name", "Give the map a name.", parent=d)
            return
        if not steps:
            messagebox.showwarning("No steps", "Add at least one step.", parent=d)
            return
        if m:
            if app.run_map == m["id"] and app.run and app.run.state in ("running", "paused", "finished"):
                messagebox.showinfo("Run in progress", "Reset the current run before editing this map's steps.", parent=d)
                return
            old = {x["name"]: x for x in m["steps"]}
            for st in steps:
                o = old.get(st["name"])
                if o:
                    for k in ("pics", "art"):
                        if o.get(k):
                            st[k] = o[k]
            m["name"], m["steps"] = nm, steps
        else:
            mid = "m%x" % int(time.time() * 1000)
            app.data["maps"].append({"id": mid, "name": nm, "steps": steps})
            app.sel = mid
        save_data(app.data)
        d.destroy()
        app.rebuild()

    def delete():
        if app.run_map == m["id"] and app.run and app.run.state in ("running", "paused"):
            messagebox.showinfo("Run in progress", "Reset the current run first.", parent=d)
            return
        if messagebox.askyesno("Delete map", f"Delete \"{m['name']}\" and its records?", parent=d):
            app.data["maps"].remove(m)
            app.data["records"].pop(m["id"], None)
            if app.sel == m["id"]:
                app.sel = app.data["maps"][0]["id"] if app.data["maps"] else None
            app.run, app.run_map = None, None
            save_data(app.data)
            d.destroy()
            app.rebuild()

    row = tk.Frame(d.body, bg=C["bg"])
    row.pack(side="bottom", fill="x", pady=(14, 0))
    Flat(row, "Save", save, C["green"], fg=C["on_green"], hover=C["green_dark"]).pack(side="right")
    Flat(row, "Cancel", d.destroy, C["card2"], hover=C["border"]).pack(side="right", padx=8)
    if m:
        Flat(row, "Delete map", delete, C["blood"], hover="#A9242E").pack(side="left")


def history_dialog(app, m):
    d = Dialog(app, f"{m['name']} - history", width=560, height=560)
    rec = app.rec(m["id"])
    tk.Label(d.body, text=m["name"], bg=C["bg"], fg=C["text"], font=(FONT, 16, "bold")).pack(anchor="w")
    pb = rec["pb_total"]
    tk.Label(d.body, text=("Personal best: " + fmt(pb)) if pb is not None else "No finished runs yet",
             bg=C["bg"], fg=C["gold"] if pb is not None else C["muted"], font=(FONT, 11)).pack(anchor="w", pady=(0, 10))
    row = tk.Frame(d.body, bg=C["bg"])
    row.pack(side="bottom", fill="x", pady=(12, 0))

    def reset_records():
        if messagebox.askyesno("Reset records", "Erase the personal best, best segments and history for this map?", parent=d):
            app.data["records"][m["id"]] = new_record()
            save_data(app.data)
            d.destroy()
            app.show_map()

    Flat(row, "Close", d.destroy, C["card2"], hover=C["border"]).pack(side="right")
    Flat(row, "Reset records", reset_records, C["blood"], hover="#A9242E").pack(side="left")
    Flat(row, "Export for LiveSplit", lambda: app.export_lss(m), C["card2"], hover=C["border"]).pack(side="left", padx=8)
    sc = Scroll(d.body, C["bg"])
    sc.pack(fill="both", expand=True)
    runs = list(reversed(rec["runs"]))[:100]
    if not runs:
        tk.Label(sc.inner, text="Nothing here yet - go finish an easter egg!", bg=C["bg"], fg=C["muted"],
                 font=(FONT, 11)).pack(pady=30)
    for r in runs:
        c = card(sc.inner, pad=10)
        c.pack(fill="x", pady=3, padx=(0, 8))
        try:
            when = dt.datetime.fromisoformat(r["date"]).strftime("%b %d, %Y  %I:%M %p").replace(" 0", " ")
        except ValueError:
            when = r["date"]
        tk.Label(c, text=when, bg=C["card"], fg=C["muted"], font=(FONT, 10)).pack(side="left")
        ok = r.get("finished")
        tk.Label(c, text=fmt(r["total"]), bg=C["card"], fg=C["good"] if ok else C["muted"],
                 font=(MONO, 13, "bold")).pack(side="right")
        if not ok:
            tk.Label(c, text="stopped early", bg=C["card"], fg=C["amber"], font=(FONT, 9)).pack(side="right", padx=10)
        elif pb is not None and abs(r["total"] - pb) < 0.005:
            tk.Label(c, text="\u2605 PB", bg=C["card"], fg=C["gold"], font=(FONT, 9, "bold")).pack(side="right", padx=10)


# ---------------------------------------------------------------- embedded icon + diagrams
ICON_B64 = (
    "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"
)

IMAGES = {
    "moon_simon": (
        "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"
        "5P8BLF7afvFEa84AAAAASUVORK5CYII="
    ),
    "sl_dials": (
        "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"
    ),
    "asc_lander": (
        "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"
        "vGUOAAAAAElFTkSuQmCC"
    ),
    "staff_lightning": (
        "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"
    ),
    "staff_fire": (
        "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"
    ),
    "staff_wind": (
        "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"
        "+ChmOHpxWQAAAABJRU5ErkJggg=="
    ),
    "staff_ice": (
        "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"
    ),
    "lightning_riddle": (
        "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"
        "rkJggg=="
    ),
}


# ---------------------------------------------------------------- pictures
PICS_DIR = os.path.join(DATA_DIR, "pics")
_IMG_CACHE = {}


def photo_for(kind, ref, max_w=520):
    # kind "art" = one of my built-in diagrams, "pic" = a picture you added yourself
    ck = (kind, ref, max_w)
    if ck in _IMG_CACHE:
        return _IMG_CACHE[ck]
    img = None
    try:
        if kind == "art":
            img = tk.PhotoImage(data=IMAGES[ref])
        else:
            path = os.path.join(PICS_DIR, ref)
            try:
                img = tk.PhotoImage(file=path)  # PNG / GIF work out of the box
            except tk.TclError:
                from PIL import Image, ImageTk  # JPG / BMP / WebP need Pillow
                pil = Image.open(path).convert("RGBA")
                if pil.width > max_w:
                    pil = pil.resize((max_w, max(1, int(pil.height * max_w / pil.width))))
                img = ImageTk.PhotoImage(pil)
        if img is not None and img.width() > max_w:
            img = img.subsample(-(-img.width() // max_w))
    except Exception:
        img = None
    _IMG_CACHE[ck] = img
    return img


def toggle_row(parent, text, getter, setter):
    row = tk.Frame(parent, bg=C["card"])
    row.pack(fill="x", pady=4)
    tk.Label(row, text=hk(text), bg=C["card"], fg=C["text"], font=(FONT, 11)).pack(side="left")
    btn = Flat(row, "", lambda: click(), C["card2"], padx=14, pady=5)
    btn.pack(side="right")

    def paint():
        on = getter()
        btn.config(text="ON" if on else "OFF")
        btn.set_colors(C["green"] if on else C["card2"], C["green_dark"] if on else C["border"],
                       C["on_green"] if on else C["text"])

    def click():
        setter(not getter())
        paint()

    paint()


def settings_dialog(app):
    d = Dialog(app, "Settings", width=620, height=740)
    s = app.data["settings"]
    bottom = tk.Frame(d.body, bg=C["bg"])
    bottom.pack(side="bottom", fill="x", pady=(10, 0))
    sc = Scroll(d.body, C["bg"])
    sc.pack(fill="both", expand=True)

    def section(title, sub=None):
        c = card(sc.inner)
        c.pack(fill="x", pady=6, padx=(0, 8))
        tk.Label(c, text=title, bg=C["card"], fg=C["text"], font=(FONT, 13, "bold")).pack(anchor="w")
        if sub:
            tk.Label(c, text=sub, bg=C["card"], fg=C["muted"], font=(FONT, 9), wraplength=500,
                     justify="left").pack(anchor="w")
        tk.Frame(c, height=6, bg=C["card"]).pack()
        return c

    def set_flag(key):
        def _set(v):
            s[key] = v
            save_data(app.data)
        return _set

    def restyle():
        # theme / font / size changes repaint everything, then reopen this window
        save_data(app.data)
        apply_theme(s)
        d.destroy()
        app.rebuild()
        app.after(80, lambda: settings_dialog(app))

    # appearance
    c = section("Appearance", "Pick a theme, then fine-tune the accent colour if you like.")
    row = tk.Frame(c, bg=C["card"])
    row.pack(fill="x")
    for name, t in THEMES.items():
        on = s.get("theme") == name and not s.get("accent")
        Flat(row, ("\u2713 " if on else "") + name, lambda n=name: (s.update(theme=n, accent=None), restyle()),
             t["green"], fg=t["on_green"], hover=t["green_dark"], padx=10, pady=7,
             font=(FONT, 9, "bold")).pack(side="left", padx=(0, 6))
    row2 = tk.Frame(c, bg=C["card"])
    row2.pack(fill="x", pady=(10, 0))

    def pick_accent():
        _, hx = colorchooser.askcolor(color=C["green"], title="Pick an accent colour", parent=d)
        if hx:
            s["accent"] = hx
            restyle()

    Flat(row2, "Custom accent colour...", pick_accent, C["green"], fg=C["on_green"], hover=C["green_dark"]).pack(side="left")
    if s.get("accent"):
        Flat(row2, "Reset accent", lambda: (s.update(accent=None), restyle()), C["card2"],
             hover=C["border"]).pack(side="left", padx=8)

    # timers
    c = section("Timers")
    toggle_row(c, "Show hundredths of a second", lambda: bool(s.get("show_cs", True)),
               lambda v: (s.update(show_cs=v), apply_theme(s), save_data(app.data)))
    tk.Label(c, text="TIMER SIZE  (%)", bg=C["card"], fg=C["muted"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(8, 0))
    size = tk.Scale(c, from_=70, to=160, orient="horizontal", length=320, bg=C["card"], fg=C["text"],
                    troughcolor=C["card2"], highlightthickness=0, activebackground=C["green"],
                    command=lambda v: s.update(timer_scale=int(float(v)) / 100))
    size.set(int(float(s.get("timer_scale", 1.0)) * 100))
    size.bind("<ButtonRelease-1>", lambda e: restyle())
    size.pack(anchor="w")
    tk.Label(c, text="TIMER FONT", bg=C["card"], fg=C["muted"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(8, 2))
    fvar = tk.StringVar(value=s.get("timer_font", "Consolas"))
    fbox = ttk.Combobox(c, textvariable=fvar, values=TIMER_FONTS, state="readonly", width=20)
    fbox.pack(anchor="w")
    fbox.bind("<<ComboboxSelected>>", lambda e: (s.update(timer_font=fvar.get()), restyle()))

    # run view
    c = section("Run view")
    toggle_row(c, "Show step notes", lambda: bool(s.get("show_notes", True)), set_flag("show_notes"))
    toggle_row(c, "Show step pictures", lambda: bool(s.get("show_images", True)), set_flag("show_images"))
    toggle_row(c, "Ask before resetting a run", lambda: bool(s.get("confirm_reset", True)), set_flag("confirm_reset"))
    toggle_row(c, "Keep window always on top", lambda: bool(s.get("topmost")),
               lambda v: (s.update(topmost=v), app.attributes("-topmost", v), save_data(app.data), app._pin_text()))

    # hotkeys
    c = section("Hotkeys", "These work even while the game has focus. Restart the app after changing them.")
    labels = {"main": "Start / complete step / finish", "undo": "Undo last step",
              "pause": "Pause / resume", "overlay": "Show / hide overlay"}
    for role in HK_ROLES:
        r = tk.Frame(c, bg=C["card"])
        r.pack(fill="x", pady=3)
        tk.Label(r, text=labels[role], bg=C["card"], fg=C["text"], font=(FONT, 11)).pack(side="left")
        var = tk.StringVar(value=s.get("hk_" + role) or HK_DEFAULTS[role])
        box = ttk.Combobox(r, textvariable=var, values=list(KEY_CHOICES), state="readonly", width=8)
        box.pack(side="right")

        def changed(e=None, role=role, var=var):
            others = {s.get("hk_" + o) or HK_DEFAULTS[o] for o in HK_ROLES if o != role}
            if var.get() in others:
                messagebox.showwarning("Key already used", "Pick a different key for this action.", parent=d)
                var.set(s.get("hk_" + role) or HK_DEFAULTS[role])
                return
            s["hk_" + role] = var.get()
            save_data(app.data)

        box.bind("<<ComboboxSelected>>", changed)

    # overlay
    c = section("Overlay contents", "Choose what the in-game overlay shows. Size, opacity and click-through live under Overlay & game.")
    for key, text in (("overlay_show_map", "Map name"), ("overlay_show_tag", "Step counter"),
                      ("overlay_show_next", "Next step"), ("overlay_show_timers", "Timers")):
        toggle_row(c, text, lambda k=key: bool(s.get(k, True)), set_flag(key))

    # data
    c = section("Your data", f"Saved automatically in {DATA_DIR}")
    r = tk.Frame(c, bg=C["card"])
    r.pack(fill="x")

    def open_folder():
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.startfile(DATA_DIR)  # Windows only
        except Exception:
            messagebox.showinfo("Data folder", DATA_DIR, parent=d)

    def backup():
        path = filedialog.asksaveasfilename(parent=d, title="Back up your data", defaultextension=".json",
                                            initialfile="BO3EETracker-backup.json")
        if path:
            try:
                save_data(app.data)
                shutil.copyfile(DATA_FILE, path)
                messagebox.showinfo("Backed up", "Saved. (Pictures you added live in the pics folder inside the data folder.)", parent=d)
            except OSError as e:
                messagebox.showerror("Couldn't back up", str(e), parent=d)

    def restore():
        path = filedialog.askopenfilename(parent=d, title="Choose a backup", filetypes=[("Backup", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data.get("maps"), list):
                raise ValueError("That doesn't look like a tracker backup.")
            if messagebox.askyesno("Restore", "Replace everything with this backup? The app will close so you can reopen it.", parent=d):
                shutil.copyfile(path, DATA_FILE)
                app.destroy()
        except (OSError, ValueError) as e:
            messagebox.showerror("Couldn't restore", str(e), parent=d)

    Flat(r, "Open data folder", open_folder, C["card2"], hover=C["border"]).pack(side="left")
    Flat(r, "Back up...", backup, C["card2"], hover=C["border"]).pack(side="left", padx=8)
    Flat(r, "Restore...", restore, C["card2"], hover=C["border"]).pack(side="left")

    def done():
        save_data(app.data)
        d.destroy()
        app.show_map()
        if app.overlay is not None:
            try:
                app.overlay._build()
            except tk.TclError:
                pass

    d.protocol("WM_DELETE_WINDOW", done)
    Flat(bottom, "Done", done, C["green"], fg=C["on_green"], hover=C["green_dark"]).pack(side="right")


# ---------------------------------------------------------------- LiveSplit export
def lss_time(t):
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:010.7f}"


def build_lss(game, category, steps, rec):
    # A LiveSplit splits file (.lss) holding your personal best and best segments.
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<Run version="1.7.0">',
           "  <GameIcon />", f"  <GameName>{escape(game)}</GameName>",
           f"  <CategoryName>{escape(category)}</CategoryName>",
           "  <Metadata>", '    <Run id="" />', '    <Platform usesEmulator="False"></Platform>',
           "    <Region></Region>", "    <Variables />", "  </Metadata>",
           "  <Offset>00:00:00</Offset>", f"  <AttemptCount>{len(rec['runs'])}</AttemptCount>",
           "  <AttemptHistory />", "  <Segments>"]
    for s in steps:
        nm = s["name"]
        pb = rec["pb_splits"].get(nm)
        best = rec["best_seg"].get(nm)
        out += ["    <Segment>", f"      <Name>{escape(nm)}</Name>", "      <Icon />", "      <SplitTimes>"]
        if pb is None:
            out.append('        <SplitTime name="Personal Best" />')
        else:
            out += ['        <SplitTime name="Personal Best">',
                    f"          <RealTime>{lss_time(pb)}</RealTime>", "        </SplitTime>"]
        out.append("      </SplitTimes>")
        if best is None:
            out.append("      <BestSegmentTime />")
        else:
            out += ["      <BestSegmentTime>", f"        <RealTime>{lss_time(best)}</RealTime>",
                    "      </BestSegmentTime>"]
        out += ["      <SegmentHistory />", "    </Segment>"]
    out += ["  </Segments>", "  <AutoSplitterSettings />", "</Run>"]
    return "\n".join(out)


# ---------------------------------------------------------------- game detection + overlay
def start_game_watch(state, get_exe):
    # Looks at the Windows process list only (like Task Manager). Never touches the game.
    if os.name != "nt":
        return

    def loop():
        while True:
            try:
                exe = (get_exe() or "BlackOps3.exe").strip()
                out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                                     capture_output=True, text=True, timeout=10,
                                     creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
                state["running"] = exe.lower() in out.lower()
            except Exception:
                state["running"] = None
            time.sleep(4)

    threading.Thread(target=loop, daemon=True).start()


def apply_overlay_style(win, click_through):
    # tool window (no taskbar button), never steals focus from the game, optional click-through
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        style |= 0x00080000 | 0x00000080 | 0x08000000  # LAYERED | TOOLWINDOW | NOACTIVATE
        style = (style | 0x20) if click_through else (style & ~0x20)  # TRANSPARENT
        user32.SetWindowLongW(hwnd, -20, style)
    except Exception:
        pass


class Overlay(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C["side"])
        s = app.data["settings"]
        self.attributes("-alpha", float(s.get("overlay_alpha", 0.85)))
        self.geometry(f"+{int(s.get('overlay_x', 40))}+{int(s.get('overlay_y', 40))}")
        self._off = (0, 0)
        self._build()
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.after(150, self.apply_styles)

    def apply_styles(self):
        apply_overlay_style(self, bool(self.app.data["settings"].get("overlay_click")))

    def _build(self):
        for w in self.winfo_children():
            w.destroy()
        st = self.app.data["settings"]
        k = float(st.get("overlay_scale", 1.0))

        def f(size, bold=False, mono=False):
            return (MONO if mono else FONT, max(7, int(size * k)), "bold" if bold else "normal")

        bg = C["side"]
        self.configure(bg=bg)
        box = tk.Frame(self, bg=bg, highlightthickness=2, highlightbackground=C["green"],
                       padx=int(14 * k), pady=int(10 * k))
        box.pack()
        head = tk.Frame(box, bg=bg)
        head.pack(fill="x")
        self.lbl = {}
        self.lbl["map"] = tk.Label(head, bg=bg, fg=C["muted"], font=f(9, True))
        if st.get("overlay_show_map", True):
            self.lbl["map"].pack(side="left")
        self.lbl["tag"] = tk.Label(head, bg=bg, fg=C["green"], font=f(9, True))
        if st.get("overlay_show_tag", True):
            self.lbl["tag"].pack(side="right", padx=(18, 0))
        self.lbl["name"] = tk.Label(box, bg=bg, fg="white", font=f(17, True), anchor="w",
                                    justify="left", wraplength=int(380 * k))
        self.lbl["name"].pack(fill="x", pady=(4, 0))
        self.lbl["next"] = tk.Label(box, bg=bg, fg=C["muted"], font=f(10), anchor="w",
                                    justify="left", wraplength=int(380 * k))
        if st.get("overlay_show_next", True):
            self.lbl["next"].pack(fill="x")
        self.timers = tk.Frame(box, bg=bg)
        self.lbl["step"] = tk.Label(self.timers, bg=bg, fg=C["amber"], font=f(20, True, True))
        self.lbl["step"].pack(side="left")
        self.lbl["total"] = tk.Label(self.timers, bg=bg, fg="white", font=f(14, True, True))
        self.lbl["total"].pack(side="right", padx=(18, 0))
        self.timers_shown = False
        self._last = {}

    def _set(self, key, text):
        if self._last.get(key) != text:
            self._last[key] = text
            self.lbl[key].config(text=hk(text))

    def update_info(self, info):
        if not info:
            self._set("map", "BO3 Easter Egg Tracker")
            self._set("tag", "")
            self._set("name", "No map selected")
            self._set("next", "")
            if self.timers_shown:
                self.timers.pack_forget()
                self.timers_shown = False
            return
        for key in ("map", "tag", "name", "next"):
            self._set(key, info[key])
        t = info.get("timers") if self.app.data["settings"].get("overlay_show_timers", True) else None
        if t:
            if not self.timers_shown:
                self.timers.pack(fill="x", pady=(6, 0))
                self.timers_shown = True
            self._set("step", fmt(t[0]))
            self._set("total", fmt(t[1]))
        elif self.timers_shown:
            self.timers.pack_forget()
            self.timers_shown = False

    def _press(self, e):
        self._off = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag(self, e):
        if self.app.data["settings"].get("overlay_click"):
            return
        self.geometry(f"+{e.x_root - self._off[0]}+{e.y_root - self._off[1]}")

    def _release(self, e):
        s = self.app.data["settings"]
        s["overlay_x"], s["overlay_y"] = self.winfo_x(), self.winfo_y()
        save_data(self.app.data)

    def reset_position(self):
        s = self.app.data["settings"]
        s["overlay_x"], s["overlay_y"] = 40, 40
        self.geometry("+40+40")
        save_data(self.app.data)


def overlay_dialog(app):
    d = Dialog(app, "Overlay & game connection", width=540, height=640)
    s = app.data["settings"]
    tk.Label(d.body, text="Overlay", bg=C["bg"], fg=C["text"], font=(FONT, 18, "bold")).pack(anchor="w")
    tk.Label(d.body, text=("A small always-on-top window showing your current step (and timers in speedrun mode). "
                           "It only looks at the Windows process list to see if the game is running - it never reads or "
                           "touches the game itself, so there's no ban risk. To see it over the game, run Black Ops 3 in "
                           "windowed or borderless-windowed mode; exclusive fullscreen hides overlays."),
             bg=C["bg"], fg=C["muted"], font=(FONT, 9), wraplength=480, justify="left").pack(anchor="w", pady=(2, 8))
    status = tk.Label(d.body, text="", bg=C["bg"], font=(FONT, 11, "bold"))
    status.pack(anchor="w")

    def refresh_status():
        try:
            if not d.winfo_exists():
                return
            g = app.game.get("running")
            if os.name != "nt":
                status.config(text="Game detection works on Windows only", fg=C["muted"])
            elif g is None:
                status.config(text="\u25CB Checking for Black Ops 3...", fg=C["muted"])
            else:
                status.config(text="\u25CF Black Ops 3 detected" if g else "\u25CB Black Ops 3 not running",
                              fg=C["green"] if g else C["muted"])
            d.after(1000, refresh_status)
        except tk.TclError:
            pass

    refresh_status()

    def toggle(text, getter, setter):
        row = tk.Frame(d.body, bg=C["bg"])
        row.pack(fill="x", pady=5)
        tk.Label(row, text=hk(text), bg=C["bg"], fg=C["text"], font=(FONT, 11)).pack(side="left")
        btn = Flat(row, "", lambda: click(), C["card2"], padx=14, pady=5)
        btn.pack(side="right")

        def paint():
            on = getter()
            btn.config(text="ON" if on else "OFF")
            btn.set_colors(C["green"] if on else C["card2"], C["green_dark"] if on else C["border"],
                           C["on_green"] if on else C["text"])

        def click():
            setter(not getter())
            paint()

        paint()

    def set_key(key):
        def _set(v):
            s[key] = v
            save_data(app.data)
            if key == "overlay_click" and app.overlay is not None:
                try:
                    app.overlay.apply_styles()
                except tk.TclError:
                    pass
        return _set

    toggle("Show overlay now   (F9)", app.overlay_visible, app.show_overlay)
    toggle("Auto-show when Black Ops 3 starts", lambda: bool(s.get("overlay_auto")), set_key("overlay_auto"))
    toggle("Click-through (mouse passes through, can't drag)", lambda: bool(s.get("overlay_click")), set_key("overlay_click"))

    def slider(text, lo, hi, init, on_change, on_release=None):
        tk.Label(d.body, text=text, bg=C["bg"], fg=C["muted"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(10, 0))
        sc = tk.Scale(d.body, from_=lo, to=hi, orient="horizontal", length=320, bg=C["bg"], fg=C["text"],
                      troughcolor=C["card2"], highlightthickness=0, activebackground=C["green"],
                      command=lambda v: on_change(int(float(v))))
        sc.set(init)
        if on_release:
            sc.bind("<ButtonRelease-1>", lambda e: on_release())
        sc.pack(anchor="w")

    def set_alpha(v):
        s["overlay_alpha"] = v / 100
        if app.overlay is not None:
            try:
                app.overlay.attributes("-alpha", v / 100)
            except tk.TclError:
                pass

    def set_scale(v):
        s["overlay_scale"] = v / 100

    def apply_scale():
        save_data(app.data)
        if app.overlay is not None:
            try:
                app.overlay._build()
            except tk.TclError:
                pass

    slider("OPACITY  (%)", 30, 100, int(float(s.get("overlay_alpha", 0.85)) * 100), set_alpha, lambda: save_data(app.data))
    slider("SIZE  (%)", 70, 200, int(float(s.get("overlay_scale", 1.0)) * 100), set_scale, apply_scale)

    d.label("GAME PROCESS NAME  (check Task Manager if it isn't detected)")
    exe = tk.StringVar(value=s.get("game_exe", "BlackOps3.exe"))
    row = tk.Frame(d.body, bg=C["bg"])
    row.pack(fill="x")
    style_entry(row, exe).pack(side="left", fill="x", expand=True, ipady=4)

    def save_exe():
        s["game_exe"] = exe.get().strip() or "BlackOps3.exe"
        save_data(app.data)

    Flat(row, "Save", save_exe, C["card2"], hover=C["border"], padx=14, pady=6).pack(side="left", padx=(8, 0))

    bottom = tk.Frame(d.body, bg=C["bg"])
    bottom.pack(side="bottom", fill="x", pady=(10, 0))

    def close():
        save_data(app.data)
        d.destroy()

    d.protocol("WM_DELETE_WINDOW", close)
    Flat(bottom, "Done", close, C["green"], fg=C["on_green"], hover=C["green_dark"]).pack(side="right")

    def reset_pos():
        if app.overlay is not None:
            try:
                app.overlay.reset_position()
            except tk.TclError:
                pass

    Flat(bottom, "Reset position", reset_pos, C["card2"], hover=C["border"]).pack(side="left")


# ---------------------------------------------------------------- app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BO3 Easter Egg Tracker")
        self.geometry("1120x840")
        self.minsize(960, 660)
        self.data = load_data()
        apply_theme(self.data["settings"])
        self.configure(bg=C["bg"])
        try:
            self._icon = tk.PhotoImage(data=ICON_B64)
            self.iconphoto(True, self._icon)
            self._logo = self._icon.subsample(2)
        except tk.TclError:
            self._icon = self._logo = None
        self.sel = self.data["maps"][0]["id"] if self.data["maps"] else None
        self.run, self.run_map = None, None
        self.last_result = None
        self.w = {}

        a = self.data.get("active")
        m = self.get_map(a.get("map_id")) if a else None
        if m:
            self.run = Run.from_dict(len(m["steps"]), a)
            self.run_map, self.sel = m["id"], m["id"]
        else:
            self.data["active"] = None

        try:
            ttk.Style(self).theme_use("clam")
        except tk.TclError:
            pass
        self.attributes("-topmost", bool(self.data["settings"].get("topmost")))

        self.hot_q = queue.Queue()
        self.hot_ok = start_global_hotkeys(self.hot_q, self.hk_vks())
        self.game = {"running": None}
        self._prev_game = False
        self.overlay = None
        start_game_watch(self.game, lambda: self.data["settings"].get("game_exe", "BlackOps3.exe"))
        for keys, fn in ((("<space>", self.hk_tk("main")), self.action_main),
                         (("<BackSpace>", self.hk_tk("undo")), self.action_undo),
                         (("<p>", self.hk_tk("pause")), self.action_pause),
                         ((self.hk_tk("overlay"),), self.toggle_overlay)):
            for k in keys:
                self.bind(k, lambda e, f=fn: f())

        self._build_shell()
        self.show_map()
        if self.data["settings"].get("overlay"):
            self.after(300, lambda: self.show_overlay(True))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(40, self.tick)

    # ---- data helpers
    def get_map(self, mid):
        return next((m for m in self.data["maps"] if m["id"] == mid), None)

    def rec(self, mid):
        r = self.data["records"].setdefault(mid, new_record())
        for k, v in new_record().items():
            r.setdefault(k, v)
        return r

    def current_run(self):
        return self.run if (self.run and self.run_map == self.sel) else None

    def save_active(self):
        r = self.run
        if r and r.state in ("running", "paused"):
            self.data["active"] = dict(r.to_dict(), map_id=self.run_map)
        else:
            self.data["active"] = None
        save_data(self.data)

    def on_close(self):
        if self.run and self.run.state == "running":
            self.run.pause()
        self.save_active()
        self.destroy()

    # ---- shell
    def _build_shell(self):
        side = tk.Frame(self, bg=C["side"], width=236)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        if getattr(self, "_logo", None):
            tk.Label(side, image=self._logo, bg=C["side"]).pack(pady=(20, 0))
        else:
            tk.Label(side, text="\u2620", bg=C["side"], fg=C["green"], font=(EMOJI, 28)).pack(pady=(24, 0))
        tk.Label(side, text="EE Tracker", bg=C["side"], fg="white", font=(FONT, 18, "bold")).pack()
        tk.Label(side, text="Black Ops 3 \u00B7 Made By Dex", bg=C["side"], fg=C["muted"],
                 font=(FONT, 9, "italic")).pack(pady=(0, 18))
        self.map_btns = {}
        self._maplist = Scroll(side, C["side"])
        self._maplist.pack(fill="both", expand=True)
        shown = None
        for m in sorted(self.data["maps"], key=lambda x: GROUP_ORDER.index(GROUP_OF.get(x["id"], "Custom"))):
            grp = GROUP_OF.get(m["id"], "Custom")
            if grp != shown:
                shown = grp
                tk.Label(self._maplist.inner, text=grp.upper(), bg=C["side"], fg=C["muted"],
                         font=(FONT, 8, "bold"), anchor="w", padx=24).pack(fill="x", pady=(10, 2))
            b = Flat(self._maplist.inner, m["name"], lambda i=m["id"]: self.select_map(i), C["side"],
                     hover=C["side_hover"], font=(FONT, 10), anchor="w", padx=24, pady=7)
            b.pack(fill="x")
            self.map_btns[m["id"]] = b

        bottom = tk.Frame(side, bg=C["side"])
        bottom.pack(side="bottom", fill="x", pady=(0, 14), before=self._maplist)
        Flat(bottom, "\u2699  Settings", lambda: settings_dialog(self), C["card2"],
             hover=C["border"], anchor="w", padx=24).pack(fill="x", padx=14, pady=3)
        Flat(bottom, "\U0001F3AE  Overlay & game...", lambda: overlay_dialog(self), C["card2"],
             hover=C["border"], anchor="w", padx=24).pack(fill="x", padx=14, pady=3)
        Flat(bottom, "+  Add map", lambda: map_dialog(self), C["card2"], hover=C["border"],
             anchor="w", padx=24).pack(fill="x", padx=14, pady=3)
        Flat(bottom, "\u21BA  Restore built-in maps", self.restore_maps, C["side"],
             fg=C["muted"], hover=C["side_hover"], font=(FONT, 9), anchor="w", padx=24, pady=6).pack(fill="x", padx=14)
        self.pin_btn = Flat(bottom, "", self.toggle_topmost, C["side"], fg=C["muted"],
                            hover=C["side_hover"], font=(FONT, 9), anchor="w", padx=24, pady=6)
        self.pin_btn.pack(fill="x", padx=14)
        self._pin_text()

        self.main = tk.Frame(self, bg=C["bg"], padx=28, pady=22)
        self.main.pack(side="left", fill="both", expand=True)

    def rebuild(self):
        keep = self.overlay
        for w in self.winfo_children():
            if w is not keep:
                w.destroy()
        self.configure(bg=C["bg"])
        self._build_shell()
        self.show_map()
        if keep is not None:
            try:
                keep._build()
            except tk.TclError:
                pass

    def _pin_text(self):
        on = bool(self.data["settings"].get("topmost"))
        self.pin_btn.config(text=f"\U0001F4CC  Always on top: {'On' if on else 'Off'}")

    def toggle_topmost(self):
        s = self.data["settings"]
        s["topmost"] = not s.get("topmost")
        self.attributes("-topmost", bool(s["topmost"]))
        self._pin_text()
        save_data(self.data)

    def restore_maps(self):
        have = {m["id"] for m in self.data["maps"]}
        added = [json.loads(json.dumps(m)) for m in DEFAULT_MAPS if m["id"] not in have]
        if not added:
            messagebox.showinfo("Built-in maps", "All the built-in maps are already here.")
            return
        self.data["maps"].extend(added)
        if not self.sel:
            self.sel = added[0]["id"]
        save_data(self.data)
        self.rebuild()

    def select_map(self, mid):
        if self.run and self.run.state in ("running", "paused") and self.run_map != mid:
            nm = self.get_map(self.run_map)["name"] if self.get_map(self.run_map) else "another map"
            messagebox.showinfo("Run in progress", f"Finish or reset your current run on {nm} first.")
            return
        if self.run_map != mid:
            self.run, self.run_map = None, None
        self.last_result = None
        self.sel = mid
        self.show_map()

    # ---- actions
    def start_run(self):
        m = self.get_map(self.sel)
        if not m or not m["steps"]:
            return
        self.run, self.run_map = Run(len(m["steps"])), m["id"]
        self.last_result = None
        self.run.start()
        self.save_active()
        self.show_map()

    def action_main(self):
        m = self.get_map(self.sel)
        if not m:
            return
        if self.mode() == "guide":
            self.guide_next()
            return
        run = self.current_run()
        if run is None or run.state == "idle":
            self.start_run()
        elif run.state == "running":
            if run.all_done():
                self.finish_run()
            else:
                self.complete(run.next_index())
        elif run.state == "paused":
            self.resume()

    def action_undo(self):
        if self.mode() == "guide":
            self.guide_undo()
            return
        run = self.current_run()
        if run and run.undo():
            self.save_active()
            self.show_map()

    def action_pause(self):
        run = self.current_run()
        if self.mode() == "guide" or not run:
            return
        if run.state == "running":
            run.pause()
        elif run.state == "paused":
            run.resume()
        else:
            return
        self.save_active()
        self.show_map()

    def resume(self):
        run = self.current_run()
        if run and run.state == "paused":
            run.resume()
            self.save_active()
            self.show_map()

    def complete(self, i):
        run = self.current_run()
        if run and run.complete(i):
            self.save_active()
            self.show_map()

    def finish_run(self):
        run, m = self.current_run(), self.get_map(self.sel)
        if not run or run.state not in ("running", "paused"):
            return
        finished = run.all_done()
        if not finished and not messagebox.askyesno(
                "Stop early?", "Some steps are still unchecked.\n\nStop the timer and save this as an unfinished run?"):
            return
        run.finish()
        is_pb, gold, prev = store_run(self.rec(m["id"]), m["steps"], run, finished)
        self.last_result = {"finished": finished, "pb": is_pb, "gold": gold, "prev": prev, "total": run.base}
        self.save_active()
        self.show_map()

    def reset_run(self):
        run = self.current_run()
        if run and run.state in ("running", "paused") and run.elapsed() > 1 and self.data["settings"].get("confirm_reset", True):
            if not messagebox.askyesno("Reset run", "Throw away this run and reset the timer?"):
                return
        self.run, self.run_map, self.last_result = None, None, None
        self.save_active()
        self.show_map()

    # ---- live updates
    def tick(self):
        try:
            while True:
                code = self.hot_q.get_nowait()
                {1: self.action_main, 2: self.action_undo, 3: self.action_pause, 4: self.toggle_overlay}.get(code, lambda: None)()
        except queue.Empty:
            pass
        run = self.current_run()
        if run and run.state in ("running", "paused"):
            try:
                if "total" in self.w:
                    self.w["total"].config(text=fmt(run.elapsed()))
                if "step" in self.w:
                    self.w["step"].config(text=fmt(run.step_elapsed()))
            except tk.TclError:
                pass
        self.tick_overlay()
        self.after(40, self.tick)

    # ---- hotkeys + pictures
    def hk_vks(self):
        s = self.data["settings"]
        out = []
        for i, role in enumerate(HK_ROLES, 1):
            name = s.get("hk_" + role) or HK_DEFAULTS[role]
            out.append((i, KEY_CHOICES.get(name, KEY_CHOICES[HK_DEFAULTS[role]])[0]))
        return tuple(out)

    def hk_tk(self, role):
        name = self.data["settings"].get("hk_" + role) or HK_DEFAULTS[role]
        return "<%s>" % KEY_CHOICES.get(name, KEY_CHOICES[HK_DEFAULTS[role]])[1]

    def guide_pictures(self, body, s, bg):
        for key in s.get("art", []):
            img = photo_for("art", key, 540)
            if img:
                tk.Label(body, image=img, bg=bg).pack(anchor="w", pady=(8, 0))
        for name in s.get("pics", []):
            img = photo_for("pic", name, 540)
            row = tk.Frame(body, bg=bg)
            row.pack(anchor="w", pady=(8, 0))
            if img:
                tk.Label(row, image=img, bg=bg).pack(anchor="w")
            else:
                tk.Label(row, text=f"(can't show {name} - use a PNG, or install Pillow for JPG)", bg=bg,
                         fg=C["muted"], font=(FONT, 9)).pack(anchor="w")
            Flat(row, "Remove picture", lambda n=name, st=s: self.remove_pic(st, n), bg, fg=C["muted"],
                 hover=C["border"], font=(FONT, 9), padx=6, pady=2).pack(anchor="w")
        Flat(body, "\U0001F4F7  Add picture", lambda st=s: self.add_pic(st),
             C["card2"] if bg == C["card"] else C["card"], fg=C["muted"], hover=C["border"],
             font=(FONT, 9, "bold"), padx=10, pady=4).pack(anchor="w", pady=(8, 0))

    def add_pic(self, step):
        path = filedialog.askopenfilename(
            title="Choose a picture for this step",
            filetypes=[("Pictures", "*.png *.gif *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return
        try:
            os.makedirs(PICS_DIR, exist_ok=True)
            name = "p%x%s" % (int(time.time() * 1000), os.path.splitext(path)[1].lower() or ".png")
            shutil.copyfile(path, os.path.join(PICS_DIR, name))
        except OSError as e:
            messagebox.showerror("Couldn't add picture", str(e))
            return
        step.setdefault("pics", []).append(name)
        save_data(self.data)
        self.show_map()

    def remove_pic(self, step, name):
        if name in step.get("pics", []):
            step["pics"].remove(name)
        try:
            os.remove(os.path.join(PICS_DIR, name))
        except OSError:
            pass
        for k in [k for k in _IMG_CACHE if k[1] == name]:
            _IMG_CACHE.pop(k, None)
        save_data(self.data)
        self.show_map()

    # ---- guide mode (steps only, no timers)
    def mode(self):
        return self.data["settings"].get("mode", "run")

    def set_mode(self, mode):
        self.data["settings"]["mode"] = mode
        save_data(self.data)
        self.show_map()

    def guide_list(self, m):
        return self.data["guide"].setdefault(m["id"], [])

    def guide_next_idx(self, m):
        done = set(self.data["guide"].get(m["id"], []))
        for i, s in enumerate(m["steps"]):
            if s["name"] not in done:
                return i
        return None

    def guide_next(self):
        m = self.get_map(self.sel)
        i = self.guide_next_idx(m) if m else None
        if i is None:
            return
        self.guide_list(m).append(m["steps"][i]["name"])
        save_data(self.data)
        self.show_map()

    def guide_undo(self):
        m = self.get_map(self.sel)
        if m and self.guide_list(m):
            self.guide_list(m).pop()
            save_data(self.data)
            self.show_map()

    def guide_toggle(self, name):
        m = self.get_map(self.sel)
        if not m:
            return
        lst = self.guide_list(m)
        if name in lst:
            lst.remove(name)
        else:
            lst.append(name)
        save_data(self.data)
        self.show_map()

    def guide_reset(self):
        m = self.get_map(self.sel)
        if m and messagebox.askyesno("Reset progress", f"Uncheck every step for {m['name']}?"):
            self.data["guide"][m["id"]] = []
            save_data(self.data)
            self.show_map()

    def toggle_hide_done(self):
        s = self.data["settings"]
        s["hide_done"] = not s.get("hide_done")
        save_data(self.data)
        self.show_map()

    def show_guide(self, m):
        steps = m["steps"]
        done = set(self.data["guide"].get(m["id"], []))
        nxt = self.guide_next_idx(m)
        n_done = sum(1 for s in steps if s["name"] in done)
        hide = bool(self.data["settings"].get("hide_done"))

        pc = card(self.main, pad=12)
        pc.pack(fill="x", pady=(10, 0))
        tk.Label(pc, text=f"{n_done} of {len(steps)} steps done", bg=C["card"], fg=C["text"],
                 font=(FONT, 13, "bold")).pack(side="left")
        Flat(pc, "Reset progress", self.guide_reset, C["blood"], hover="#A9242E",
             padx=12, pady=5).pack(side="right")
        Flat(pc, f"Hide completed: {'On' if hide else 'Off'}", self.toggle_hide_done, C["card2"],
             hover=C["border"], padx=12, pady=5).pack(side="right", padx=8)
        bar = tk.Frame(self.main, bg=C["border"], height=8)
        bar.pack(fill="x", pady=(8, 0))
        fill = tk.Frame(bar, bg=C["green"])
        fill.place(relx=0, rely=0, relheight=1, relwidth=(n_done / len(steps)) if steps else 0)

        sc = Scroll(self.main, C["bg"])
        sc.pack(fill="both", expand=True, pady=(10, 0))
        for i, s in enumerate(steps):
            is_done = s["name"] in done
            if is_done and hide:
                continue
            is_next = i == nxt
            bg = C["card2"] if is_next else C["card"]
            c = card(sc.inner, bg=bg, border=C["green"] if is_next else C["border"], pad=12)
            c.pack(fill="x", pady=4, padx=(0, 8))
            icon = tk.Label(c, text="\u2713" if is_done else ("\u25B6" if is_next else "\u25CB"), bg=bg,
                            fg=C["green"] if is_done else (C["amber"] if is_next else C["muted"]),
                            font=(FONT, 16, "bold"), width=2, cursor="hand2", anchor="n")
            icon.pack(side="left", anchor="n")
            icon.bind("<Button-1>", lambda e, nm=s["name"]: self.guide_toggle(nm))
            body = tk.Frame(c, bg=bg)
            body.pack(side="left", fill="x", expand=True)
            tk.Label(body, text=f"{i + 1}.  {s['name']}" + ("      \u25B6 NEXT" if is_next else ""), bg=bg,
                     fg=C["muted"] if is_done else C["text"], font=(FONT, 13, "bold"), anchor="w",
                     justify="left", wraplength=620).pack(anchor="w")
            if s.get("note"):
                tk.Label(body, text=s["note"], bg=bg, fg=C["muted"], font=(FONT, 10), anchor="w",
                         justify="left", wraplength=620).pack(anchor="w", pady=(2, 0))
            for line in s.get("details", []):
                tk.Label(body, text="\u2022  " + line, bg=bg, fg=C["muted"] if is_done else C["text"],
                         font=(FONT, 10), anchor="w", justify="left", wraplength=600).pack(
                    anchor="w", pady=(3, 0), padx=(8, 0))
            self.guide_pictures(body, s, bg)
        if nxt is not None and not hide and len(steps) > 4:
            frac = max(0.0, (nxt - 1) / max(1, len(steps)))
            self.after(60, lambda: sc.canvas.yview_moveto(frac))

        hint = ("Guide hotkeys work in-game too:  F6 check next step   \u00B7   F7 undo   \u00B7   F9 overlay"
                if self.hot_ok else
                "Hotkeys (click this window first):  F6 or Space = check next step   \u00B7   F7 or Backspace = undo   \u00B7   F9 = overlay")
        tk.Label(self.main, text=hk(hint), bg=C["bg"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=(8, 0))

    # ---- overlay + export
    def overlay_visible(self):
        try:
            return self.overlay is not None and bool(self.overlay.winfo_exists()) and self.overlay.state() == "normal"
        except tk.TclError:
            return False

    def show_overlay(self, on):
        s = self.data["settings"]
        try:
            if on:
                if self.overlay is None or not self.overlay.winfo_exists():
                    self.overlay = Overlay(self)
                else:
                    self.overlay.deiconify()
                    self.overlay.apply_styles()
                s["overlay"] = True
            else:
                if self.overlay is not None and self.overlay.winfo_exists():
                    self.overlay.withdraw()
                s["overlay"] = False
        except tk.TclError:
            return
        save_data(self.data)

    def toggle_overlay(self):
        self.show_overlay(not self.overlay_visible())

    def overlay_info(self):
        guide = self.mode() == "guide"
        run = self.run if (self.run and self.run.state in ("running", "paused", "finished")) else None
        m = self.get_map(self.run_map if (run and not guide) else self.sel)
        if not m or not m["steps"]:
            return None
        steps, n = m["steps"], len(m["steps"])
        info = {"map": m["name"] + ("  \u00B7  guide" if guide else ""), "timers": None, "next": ""}
        paused = False
        if guide:
            i = self.guide_next_idx(m)
            if i is None:
                info.update(tag="DONE", name="Guide complete \u2713")
                return info
        elif run:
            if run.state == "finished":
                info.update(tag="COMPLETE", name=f"Finished in {fmt(run.base)}")
                return info
            i = run.next_index()
            info["timers"] = (run.step_elapsed(), run.elapsed())
            paused = run.state == "paused"
            if i is None:
                info.update(tag="ALL STEPS DONE", name="Press F6 to finish")
                return info
        else:
            i = 0
        tag = f"STEP {i + 1}/{n}"
        if paused:
            tag += "  \u23F8 PAUSED"
        elif not guide and not run:
            tag += "  \u00B7  READY (F6)"
        info.update(tag=tag, name=steps[i]["name"],
                    next=f"Next: {steps[i + 1]['name']}" if i + 1 < n else "Last step")
        return info

    def tick_overlay(self):
        g = self.game.get("running")
        if g is not None:
            if self.data["settings"].get("overlay_auto"):
                if g and not self._prev_game:
                    self.show_overlay(True)
                elif not g and self._prev_game:
                    self.show_overlay(False)
            self._prev_game = g
        if self.overlay_visible():
            try:
                self.overlay.update_info(self.overlay_info())
            except tk.TclError:
                pass

    def export_lss(self, m):
        path = filedialog.asksaveasfilename(
            title="Export for LiveSplit", defaultextension=".lss",
            initialfile=f"{m['name']} Easter Egg.lss",
            filetypes=[("LiveSplit splits", "*.lss"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(build_lss("Call of Duty: Black Ops III", f"{m['name']} Easter Egg",
                                  m["steps"], self.rec(m["id"])))
            messagebox.showinfo("Exported", "Saved. In LiveSplit, right-click > Open Splits > From File.")
        except OSError as e:
            messagebox.showerror("Couldn't export", str(e))

    # ---- main view
    def show_map(self):
        for b_id, b in getattr(self, "map_btns", {}).items():
            try:
                if b_id == self.sel:
                    b.set_colors(C["green"], C["green"], C["on_green"])
                else:
                    b.set_colors(C["side"], C["side_hover"], C["text"])
            except tk.TclError:
                pass
        for w in self.main.winfo_children():
            w.destroy()
        self.w = {}
        m = self.get_map(self.sel)
        if not m:
            tk.Label(self.main, text="No maps yet. Click \"+ Add map\" to create one.", bg=C["bg"],
                     fg=C["muted"], font=(FONT, 13)).pack(pady=60)
            return

        rec = self.rec(m["id"])
        steps = m["steps"]
        run = self.current_run()
        state = run.state if run else "idle"
        names = [s["name"] for s in steps]

        # header
        top = tk.Frame(self.main, bg=C["bg"])
        top.pack(fill="x")
        tk.Label(top, text=m["name"], bg=C["bg"], fg=C["text"], font=(FONT, 24, "bold")).pack(side="left")
        Flat(top, "Edit steps", lambda: map_dialog(self, m), C["card2"], hover=C["border"],
             padx=14, pady=6).pack(side="right")
        Flat(top, "History", lambda: history_dialog(self, m), C["card2"], hover=C["border"],
             padx=14, pady=6).pack(side="right", padx=8)

        tabs = tk.Frame(self.main, bg=C["bg"])
        tabs.pack(fill="x", pady=(10, 0))
        for key, label in (("run", "\u23F1  Speedrun"), ("guide", "\U0001F4D6  Guide")):
            on = self.mode() == key
            Flat(tabs, label, lambda k=key: self.set_mode(k), C["green"] if on else C["card2"],
                 fg=C["on_green"] if on else C["text"], hover=C["green_dark"] if on else C["border"],
                 padx=18, pady=7).pack(side="left", padx=(0, 6))
        if self.mode() == "guide":
            self.show_guide(m)
            return

        # timer card
        tc = card(self.main, pad=16)
        tc.pack(fill="x", pady=(12, 0))
        left = tk.Frame(tc, bg=C["card"])
        left.pack(side="left")
        colors = {"idle": C["muted"], "running": C["green"], "paused": C["amber"], "finished": C["gold"]}
        labels = {"idle": "READY", "running": "RUNNING", "paused": "PAUSED", "finished": "COMPLETE"}
        tk.Label(left, text=labels[state], bg=C["card"], fg=colors[state], font=(FONT, 9, "bold")).pack(anchor="w")
        self.w["total"] = tk.Label(left, text=fmt(run.elapsed() if run else 0), bg=C["card"],
                                   fg=colors[state], font=(MONO, tsz(50), "bold"))
        self.w["total"].pack(anchor="w")

        right = tk.Frame(tc, bg=C["card"])
        right.pack(side="right")
        sob = None
        if steps and all(n in rec["best_seg"] for n in names):
            sob = sum(rec["best_seg"][n] for n in names)
        done_n = len(run.done) if run else 0
        stats = [("STEPS", f"{done_n} / {len(steps)}", C["text"]),
                 ("PERSONAL BEST", fmt(rec["pb_total"]) if rec["pb_total"] is not None else "--", C["gold"]),
                 ("SUM OF BEST", fmt(sob) if sob is not None else "--", C["text"])]
        for i, (lab, val, col) in enumerate(stats):
            tk.Label(right, text=lab, bg=C["card"], fg=C["muted"], font=(FONT, 8, "bold")).grid(row=0, column=i, padx=14, sticky="w")
            tk.Label(right, text=val, bg=C["card"], fg=col, font=(MONO, 15, "bold")).grid(row=1, column=i, padx=14, sticky="w")

        # controls
        ctl = tk.Frame(self.main, bg=C["bg"])
        ctl.pack(fill="x", pady=(10, 0))
        big = dict(font=(FONT, 12, "bold"), padx=22, pady=11)
        if state == "idle":
            Flat(ctl, "\u25B6  Start run   (F6 / Space)", self.start_run, C["green"], fg=C["on_green"],
                 hover=C["green_dark"], **big).pack(side="left")
        elif state in ("running", "paused"):
            if state == "paused":
                Flat(ctl, "\u25B6  Resume   (F8)", self.resume, C["green"], fg=C["on_green"],
                     hover=C["green_dark"], **big).pack(side="left")
            elif run.all_done():
                tk.Label(ctl, text="All steps done \u2192 press Finish", bg=C["bg"], fg=C["gold"],
                         font=(FONT, 12, "bold")).pack(side="left", padx=(0, 6))
            else:
                Flat(ctl, "\u2713  Complete step   (F6)", lambda: self.complete(run.next_index()),
                     C["green"], fg=C["on_green"], hover=C["green_dark"], **big).pack(side="left")
            if state == "running":
                Flat(ctl, "\u23F8  Pause", self.action_pause, C["card2"], hover=C["border"],
                     font=(FONT, 11, "bold"), padx=16, pady=11).pack(side="left", padx=8)
            Flat(ctl, "\u21B6  Undo   (F7)", self.action_undo, C["card2"], hover=C["border"],
                 font=(FONT, 11, "bold"), padx=16, pady=11).pack(side="left")
            ready = run.all_done()
            Flat(ctl, "\U0001F3C1  FINISH EASTER EGG", self.finish_run,
                 C["gold"] if ready else C["card2"], fg=C["on_green"] if ready else C["muted"],
                 hover="#FFD966" if ready else C["border"], font=(FONT, 12, "bold"),
                 padx=20, pady=11).pack(side="left", padx=8)
            Flat(ctl, "\u27F2  Reset", self.reset_run, C["blood"], hover="#A9242E",
                 font=(FONT, 11, "bold"), padx=16, pady=11).pack(side="right")
        else:  # finished
            Flat(ctl, "New run", self.reset_run, C["green"], fg=C["on_green"],
                 hover=C["green_dark"], **big).pack(side="left")

        # current step / result card
        cur = run.next_index() if run and state in ("idle", "running", "paused") else None
        if state == "idle" and steps:
            cur = 0
        if state == "finished" and self.last_result:
            res = self.last_result
            rc = card(self.main, bg=C["card2"], border=C["gold"], pad=16)
            rc.pack(fill="x", pady=(10, 0))
            if res["finished"]:
                tk.Label(rc, text="\U0001F3C1  EASTER EGG COMPLETE", bg=C["card2"], fg=C["gold"],
                         font=(FONT, 16, "bold")).pack(anchor="w")
                if res["pb"]:
                    msg = "\u2605 New personal best!" + (f"  ({fmt_delta(res['total'] - res['prev'])} vs old PB)" if res["prev"] is not None else "")
                    col = C["gold"]
                else:
                    msg = f"{fmt_delta(res['total'] - res['prev'])} vs your PB of {fmt(res['prev'])}"
                    col = C["good"] if res["total"] <= res["prev"] else C["red"]
                tk.Label(rc, text=msg, bg=C["card2"], fg=col, font=(FONT, 12, "bold")).pack(anchor="w", pady=(2, 0))
                if res["gold"]:
                    tk.Label(rc, text=f"{res['gold']} new best segment{'s' if res['gold'] != 1 else ''}",
                             bg=C["card2"], fg=C["gold"], font=(FONT, 10)).pack(anchor="w")
            else:
                tk.Label(rc, text="Run stopped early", bg=C["card2"], fg=C["amber"],
                         font=(FONT, 15, "bold")).pack(anchor="w")
                tk.Label(rc, text="Saved to history. Unfinished runs don't count for your personal best.",
                         bg=C["card2"], fg=C["muted"], font=(FONT, 10)).pack(anchor="w")
        elif cur is not None and cur < len(steps):
            st = steps[cur]
            cc = card(self.main, bg=C["card2"], border=C["green"] if state == "running" else C["border"], pad=14)
            cc.pack(fill="x", pady=(10, 0))
            head = tk.Frame(cc, bg=C["card2"])
            head.pack(fill="x")
            tag = {"idle": "UP FIRST", "running": "NOW", "paused": "PAUSED ON"}[state]
            lf = tk.Frame(head, bg=C["card2"])
            lf.pack(side="left", fill="x", expand=True)
            tk.Label(lf, text=f"{tag}  \u00B7  STEP {cur + 1} OF {len(steps)}", bg=C["card2"], fg=C["green"],
                     font=(FONT, 9, "bold")).pack(anchor="w")
            tk.Label(lf, text=st["name"], bg=C["card2"], fg=C["text"], font=(FONT, 17, "bold"),
                     wraplength=640, justify="left").pack(anchor="w")
            rf = tk.Frame(head, bg=C["card2"])
            rf.pack(side="right")
            self.w["step"] = tk.Label(rf, text=fmt(run.step_elapsed() if run else 0), bg=C["card2"],
                                      fg=C["amber"] if state != "idle" else C["muted"], font=(MONO, tsz(28), "bold"))
            self.w["step"].pack(anchor="e")
            best = rec["best_seg"].get(st["name"])
            tk.Label(rf, text=f"best {fmt(best)}" if best is not None else "no best yet", bg=C["card2"],
                     fg=C["muted"], font=(FONT, 9)).pack(anchor="e")
            if st.get("note") and self.data["settings"].get("show_notes", True):
                tk.Label(cc, text=st["note"], bg=C["card2"], fg=C["muted"], font=(FONT, 10),
                         wraplength=900, justify="left").pack(anchor="w", pady=(6, 0))
            if self.data["settings"].get("show_images", True):
                refs = [("art", k) for k in st.get("art", [])] + [("pic", n) for n in st.get("pics", [])]
                if refs:
                    prow = tk.Frame(cc, bg=C["card2"])
                    prow.pack(anchor="w", pady=(8, 0))
                    for kind, ref in refs[:3]:
                        img = photo_for(kind, ref, 280)
                        if img:
                            tk.Label(prow, image=img, bg=C["card2"]).pack(side="left", padx=(0, 8))

        # checklist
        sc = Scroll(self.main, C["bg"])
        sc.pack(fill="both", expand=True, pady=(12, 0))
        pb_splits = rec["pb_splits"]
        for i, st in enumerate(steps):
            d = run.done.get(i) if run else None
            is_cur = (i == cur) and state in ("running", "paused")
            bg = C["card2"] if is_cur else C["card"]
            row = card(sc.inner, bg=bg, border=C["green"] if is_cur else C["border"], pad=8)
            row.pack(fill="x", pady=2, padx=(0, 8))
            icon = "\u2713" if d else ("\u25B6" if is_cur else "\u25CB")
            icol = C["green"] if d else (C["amber"] if is_cur else C["muted"])
            ic = tk.Label(row, text=icon, bg=bg, fg=icol, font=(FONT, 13, "bold"), width=2)
            ic.pack(side="left")
            nm = tk.Label(row, text=f"{i + 1}.  {st['name']}", bg=bg,
                          fg=C["muted"] if d else C["text"], font=(FONT, 11, "bold" if is_cur else "normal"),
                          anchor="w")
            nm.pack(side="left", fill="x", expand=True)
            kids = [row, ic, nm]
            if d:
                best = rec["best_seg"].get(st["name"])
                golden = best is not None and d["dur"] < best
                t = tk.Label(row, text=fmt(d["dur"]), bg=bg, fg=C["gold"] if golden else C["text"],
                             font=(MONO, 12, "bold"))
                t.pack(side="right", padx=(10, 4))
                kids.append(t)
                pbs = pb_splits.get(st["name"])
                if pbs is not None:
                    dl = d["split"] - pbs
                    dlab = tk.Label(row, text=fmt_delta(dl), bg=bg, fg=C["good"] if dl <= 0 else C["red"],
                                    font=(MONO, 11))
                    dlab.pack(side="right", padx=8)
                    kids.append(dlab)
            else:
                best = rec["best_seg"].get(st["name"])
                if best is not None:
                    b = tk.Label(row, text=f"best {fmt(best)}", bg=bg, fg=C["muted"], font=(FONT, 9))
                    b.pack(side="right", padx=6)
                    kids.append(b)
                if state == "running":
                    for k in kids:
                        k.configure(cursor="hand2")
                        k.bind("<Button-1>", lambda e, n=i: self.complete(n))
        if cur is not None and len(steps) > 6:
            frac = max(0.0, (cur - 2) / max(1, len(steps)))
            self.after(60, lambda: sc.canvas.yview_moveto(frac))

        hint = ("Hotkeys work even while the game is in focus:  F6 start / next step / finish   \u00B7   F7 undo   \u00B7   F8 pause"
                if self.hot_ok else
                "Hotkeys (click this window first):  F6 or Space = start / next step / finish   \u00B7   F7 or Backspace = undo   \u00B7   F8 or P = pause")
        hint += "   \u00B7   F9 overlay"
        tk.Label(self.main, text=hk(hint), bg=C["bg"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=(8, 0))


if __name__ == "__main__":
    try:  # crisp text on high-DPI Windows screens
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:  # so the taskbar shows our icon instead of Python's
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID("BO3EETracker.App")
    except Exception:
        pass
    App().mainloop()