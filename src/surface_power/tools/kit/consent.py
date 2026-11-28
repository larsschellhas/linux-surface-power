"""The disclaimer and the questions, all before the first test (spec tester-kit.md 6.3).

One answer per risk class. `--yes` answers the read and load questions only: it never accepts the disclaimer, the module tests
or the writing.
"""

DISCLAIMER = (
    "Before you start. This is experimental software. We have tried to make every test as safe as we can: each one checks its "
    "conditions first, the module tests stop at the first unexpected result, the check undoes what it changed, and no step sets a "
    "power limit higher than the ones your machine already has. For the measurements it pauses services that manage power, such "
    "as thermald, so their throttling is off during that time; the processor's own overheat protection stays active. We do not "
    "expect anything to go wrong. We cannot guarantee it, and many models have never run these tests.",
    "What can happen: the machine may freeze or restart, and unsaved work is lost. Under load it gets warm and the fan may get "
    "loud. The later steps load a kernel module that is not part of your kernel (the kernel stays marked \"tainted\" until the "
    "next boot), and the last one writes a power limit.",
    "You run this on your own device at your own risk, and you are responsible for it. The software comes without any warranty "
    "(see the licence files). Save your work, close other programs and connect the charger.")

# Shown before the question "may we collect this?". It has to stay true: DISCLOSED names the entry of this list under which each
# part of the result's data falls, and a test fails for a part that is in no entry (tests/test_kit_run.py).
COLLECTED = (
    ("Your device", "model and SKU, BIOS version and date, processor"),
    ("Your system", "Linux distribution and kernel, Secure Boot, lockdown and module signing, a few kernel options about power, how "
                    "long it has been running and how often it slept, date, time and time zone, the version of this checkout"),
    ("Power software", "whether thermald, tuned, TLP, power-profiles-daemon and this project's daemon run (thermald with the "
                       "names of its options), and what the check pauses"),
    ("Power and heat", "the power limits and every change to them during the run, temperatures, fan speeds, battery and charger "
                       "values from the Surface's controller, the list of the controller's internal devices and the kernel's name "
                       "for the Surface's hub, and the check's questions to the controller with its raw answers"),
    ("Device state", "what Surface power management reacts to: power mode, charger, battery charge and current, lid, tablet or "
                     "laptop position, screen brightness, whether an external screen or a camera is in use, connected docks, game "
                     "controllers and USB-C power sources; on some models the graphics card's temperature and power"),
    ("Power settings", "of Microsoft's power configuration on your device only a fingerprint, its size and the kinds of rules in "
                       "it, never its content; and the limits computed from it"),
    ("Logs", "what this project's daemon logs during its trial run, and kernel log lines about processor registers, hardware "
             "errors and the test module (such a line can name the program concerned)"),
    ("Test results", "what each step of the check found, and what you type at the end, if anything"),
)
NOT_COLLECTED = ("No serial numbers, nothing from your files, no history of what you did, no picture and no sound. User names, "
                 "the computer's name, home folder paths and network hardware (MAC) addresses are removed before the file is saved.")
WHERE_IT_GOES = ("One text file in the folder you started the check from. Nothing is sent anywhere: read it, then decide whether to "
                 "post it. Issues on GitHub are public. At the end the check can post it for you, only if you say yes.")
HOW = ("By reading only, and by a trial run of this project's daemon that writes nothing. To ask the Surface's controller, the "
       "check loads surface_aggregator_cdev (part of linux-surface) if needed and unloads it again; the same goes for msr (part of "
       "your kernel), which gives access to processor registers. Where your kernel does not "
       "provide temperature sensors your device needs, the check asks the controller for them directly.")
DISCLOSED = {"run": "Your system", "machine": "Your device", "system": "Your system", "software": "Power software",
             "paused": "Power software", "limits": "Power and heat", "watch": "Power and heat", "sam_devices": "Power and heat",
             "sensors": "Power and heat", "ec_attributes": "Power and heat", "sensor_probe": "Power and heat",
             "load": "Power and heat", "write": "Power and heat", "daemon": "Power settings", "blob": "Power settings",
             "dry_run": "Device state", "kernel_messages": "Logs", "module": "Logs", "stages": "Test results",
             "tester_note": "Test results", "ec_requests": "Power and heat", "kernel_nodes": "Power and heat"}

ACCEPT = "i accept"
WRITE_WORD = "write"

CLASSES = ("read", "load", "module", "write")
TITLES = {"read": "Collect data", "load": "Load test", "module": "Kernel module tests", "write": "First write test"}
QUESTIONS = {
    "read": "May the check collect this? (about 1 minute; 3 without the load test)",
    "load": "Run the load test (about 5 minutes; the machine gets warm, the fan may get loud)?",
    "module": "Run the kernel module tests (loads a module that is not part of your kernel)?",
    "write": "Run the first write test (lowers the MMIO power limit for a few minutes)?",
}
# the short line the tester answers in; the full question stands above it
PROMPTS = {"read": "Collect data", "load": "Run the load test", "module": "Run the kernel module tests",
           "write": f"First write test: type \"{WRITE_WORD}\", or press Enter to skip"}


class Consent:
    def __init__(self, ask_line, term, assume_yes: bool = False):
        """`ask_line(prompt) -> str` reads the tester's answer ("" at end of input); `term` is the kit's console."""
        self.ask_line, self.term, self.assume_yes = ask_line, term, assume_yes
        self.accepted = False
        self.given: dict[str, bool] = {}

    def disclaimer(self) -> bool:
        self.term.heading("Please read this first")
        for i, paragraph in enumerate(DISCLAIMER):
            if i:
                self.term.line()
            self.term.text(paragraph)
        self.term.line()
        self.accepted = self.ask_line('  > Type "I accept" to go on: ').strip().strip('"').lower() == ACCEPT
        return self.accepted

    def show_collected(self) -> None:
        self.term.line()
        self.term.text("What is collected", 2, "bold")
        self.term.rows(list(COLLECTED))
        for title, text in (("What is not", NOT_COLLECTED), ("Where it goes", WHERE_IT_GOES), ("How", HOW)):
            self.term.line()
            self.term.text(title, 2, "bold")
            self.term.text(text, 2)

    def ask(self, kind: str, notes: list[str] = ()) -> bool:
        """One risk class; `notes` are shown first (what is known about this model, what gets paused)."""
        self.term.heading(f"Question: {TITLES[kind]}")
        for note in notes:
            self.term.bullet(note)
        if kind == "read":
            self.show_collected()
        self.term.line()
        self.term.text(QUESTIONS[kind], 2, "bold")
        if self.assume_yes and kind in ("read", "load"):
            self.term.text("yes (--yes)", 2)
            answer = True
        elif kind == "write":
            answer = self.ask_line(f"  > {PROMPTS[kind]}: ").strip().strip('"').lower() == WRITE_WORD
        else:
            answer = self.ask_line(f"  > {PROMPTS[kind]} [y/N]: ").strip().lower() in ("y", "yes", "j", "ja")
        self.given[kind] = answer
        return answer
