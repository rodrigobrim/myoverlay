"""Finding, choosing and joining a logger's access point.

Two layers: the netsh scan parser on its own, then a simulated WiFi world
(`Situation`) that drives the real Transport end to end - what is in range,
which networks are protected, what the credential store already knows, and
what the user types at each prompt.
"""

import types

import pytest

from media_tools.aim import UserInputNeededError, discovery
# From the submodule, not the package: `media_tools.aim.connect` is the
# re-exported function there, which would shadow the module.
from media_tools.aim.connect import TRANSPORTS, connect
from media_tools.aim.mychron.v6 import wifi

# ---------------------------------------------------------------- the parser

# netsh wlan show networks mode=bssid, trimmed, as a localized (pt-BR) Windows
# prints it: the 'SSID n :' headers and the WPA2-Personal values stay in
# English, everything around them does not.
SCAN = """
Nome da interface: Wi-Fi
Ha 3 redes visiveis no momento.

SSID 1 : AiM-MYC6-021763-MyChron6 Brim
    Tipo de rede            : Infraestrutura
    Autenticacao            : Aberta
    Criptografia            : Nenhuma
    BSSID 1                 : 00:11:22:33:44:55
         Sinal             : 99%

SSID 2 : Sissilandia
    Tipo de rede            : Infraestrutura
    Autenticacao            : WPA2-Personal
    Criptografia            : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Sinal             : 96%

SSID 3 : AiM-MYC6-999999-Spare
    Tipo de rede            : Infraestrutura
    Autenticacao            : WPA2-Personal
    Criptografia            : CCMP
    BSSID 1                 : 11:22:33:44:55:66
         Sinal             : 55%
"""


@pytest.fixture
def scan(monkeypatch):
    def use(text):
        monkeypatch.setattr(discovery, "_force_scan", lambda: None)
        monkeypatch.setattr(
            discovery, "_netsh", lambda *a: types.SimpleNamespace(stdout=text))
    return use


def test_find_logger_aps_reads_names_and_security(scan):
    """The house network's WPA2 must not rub off on the logger above it."""
    scan(SCAN)
    assert discovery.find_logger_aps() == [
        discovery.LoggerAp("AiM-MYC6-021763-MyChron6 Brim", False),
        discovery.LoggerAp("AiM-MYC6-999999-Spare", True),
    ]


def test_find_logger_aps_ignores_non_aim_and_empty_scans(scan):
    scan("SSID 1 : Sissilandia\n    Autenticacao : WPA2-Personal\n")
    assert discovery.find_logger_aps() == []
    scan("")
    assert discovery.find_logger_aps() == []


def test_find_logger_aps_survives_netsh_failure(monkeypatch):
    def boom(*a):
        raise OSError("netsh missing")

    monkeypatch.setattr(discovery, "_force_scan", lambda: None)
    monkeypatch.setattr(discovery, "_netsh", boom)
    assert discovery.find_logger_aps() == []


def test_find_logger_aps_scans_before_reading(monkeypatch):
    """netsh reports the previous scan; while associated to an AP, Windows
    rescans so rarely that the cache can hold only the connected network -
    a powered-on logger stays invisible unless a scan is forced first."""
    order = []
    monkeypatch.setattr(discovery, "_force_scan",
                        lambda: order.append("scan"))
    monkeypatch.setattr(discovery, "_netsh", lambda *a: (
        order.append("netsh"), types.SimpleNamespace(stdout=SCAN))[1])
    assert len(discovery.find_logger_aps()) == 2
    assert order == ["scan", "netsh"]


def test_force_scan_failure_is_not_fatal(monkeypatch):
    """No PowerShell, no adapter, denied consent: the cache is still read."""
    def boom(*a, **kw):
        raise OSError("powershell missing")

    monkeypatch.setattr(discovery.subprocess, "run", boom)
    discovery._force_scan()          # must not raise


# ------------------------------------------------------------ the fake world
OPEN = None            # an access point with no password
A = "AiM-MYC6-021763-MyChron6 Brim"
B = "AiM-MYC6-999999-Spare"


class Situation:
    """A WiFi world the real Transport can be run against.

    `aps` maps SSID -> the password that AP accepts (OPEN for none). `stored`
    is what credstore already holds. `picks` and `typed` are the answers the
    user gives at the choice and password prompts, in order; running out of
    either fails the test, so "no prompt expected" is enforced rather than
    assumed.
    """

    def __init__(self, monkeypatch, aps, stored=None, picks=(), typed=(),
                 interactive=True):
        self.aps = dict(aps)
        self.stored = dict(stored or {})
        self.joined = None
        self.attempts = []        # (ssid, password) per join try
        self.saved = []           # (ssid, password) written to credstore
        self.asked_to_pick = 0
        self.asked_for_password = []
        self._picks = iter(picks)
        self._typed = iter(typed)

        monkeypatch.setattr(discovery, "_force_scan", lambda: None)
        monkeypatch.setattr(discovery, "_netsh", self._netsh)
        monkeypatch.setattr(discovery, "join_logger_ap", self._join)
        monkeypatch.setattr(discovery, "wifi_available",
                            lambda timeout=1.5: self.joined is not None)
        monkeypatch.setattr(discovery, "leave_logger_ap", lambda: None)
        monkeypatch.setattr(wifi.credstore, "get", self.stored.get)
        monkeypatch.setattr(wifi.credstore, "save", self._save)
        monkeypatch.setattr(wifi, "_interactive", lambda: interactive)
        monkeypatch.setattr(wifi.getpass, "getpass", self._prompt_password)
        monkeypatch.setattr("builtins.input", self._prompt_pick)
        monkeypatch.setattr(wifi.socket, "create_connection", self._socket)
        monkeypatch.setattr(wifi.Transport, "_hello", lambda _self: b"hi")

    # -- the radio ---------------------------------------------------------
    def _netsh(self, *args):
        blocks = []
        for i, (ssid, password) in enumerate(self.aps.items(), 1):
            auth = "Aberta" if password is OPEN else "WPA2-Personal"
            blocks.append(
                f"SSID {i} : {ssid}\n"
                f"    Autenticacao            : {auth}\n"
                f"    BSSID 1                 : 00:11:22:33:44:0{i}\n"
                f"         Sinal             : 90%\n")
        return types.SimpleNamespace(
            stdout="Nome da interface: Wi-Fi\n\n" + "\n".join(blocks))

    def _join(self, ssid, password=None, timeout=30.0):
        self.attempts.append((ssid, password))
        if self.aps.get(ssid, "absent") == password:
            self.joined = ssid
            return True
        return False

    def _socket(self, address, timeout=None):
        if self.joined is None:
            raise OSError("no route to host")
        return types.SimpleNamespace(close=lambda: None)

    # -- the user and the store -------------------------------------------
    def _save(self, ssid, password):
        self.saved.append((ssid, password))
        self.stored[ssid] = password

    def _prompt_pick(self, *args):
        self.asked_to_pick += 1
        try:
            return next(self._picks)
        except StopIteration:
            raise AssertionError("asked to pick a logger unexpectedly") from None

    def _prompt_password(self, prompt):
        self.asked_for_password.append(prompt)
        try:
            return next(self._typed)
        except StopIteration:
            raise AssertionError("asked for a password unexpectedly") from None

    def run(self):
        """Connect exactly as `mt telemetry get` would over WiFi."""
        return wifi.Transport()


@pytest.fixture
def situation(monkeypatch):
    def build(aps, **kw):
        return Situation(monkeypatch, aps, **kw)
    return build


# ------------------------------------------------------- one logger in range
def test_single_open_logger_needs_no_input(situation):
    s = situation({A: OPEN})
    s.run()
    assert s.joined == A
    assert s.attempts == [(A, None)]
    assert s.asked_to_pick == 0 and s.asked_for_password == []
    assert s.saved == []


def test_single_protected_logger_asks_once_then_remembers(situation):
    s = situation({A: "pit-lane"}, typed=["pit-lane"])
    s.run()
    assert s.joined == A
    assert len(s.asked_for_password) == 1
    assert s.asked_to_pick == 0        # only one logger: nothing to choose
    assert s.saved == [(A, "pit-lane")]


def test_stored_password_is_reused_without_asking(situation):
    s = situation({A: "pit-lane"}, stored={A: "pit-lane"})
    s.run()
    assert s.joined == A
    assert s.asked_for_password == []
    assert s.saved == []               # nothing to rewrite


def test_stale_stored_password_is_replaced(situation, capsys):
    """The logger's password changed since it was stored."""
    s = situation({A: "new-pw"}, stored={A: "old-pw"}, typed=["new-pw"])
    s.run()
    assert s.attempts == [(A, "old-pw"), (A, "new-pw")]
    assert s.saved == [(A, "new-pw")]
    assert "no longer works" in capsys.readouterr().err


def test_wrong_password_does_not_reach_the_store(situation):
    s = situation({A: "pit-lane"}, typed=["guess", "guess", "guess"])
    with pytest.raises(OSError, match="wrong password"):
        s.run()
    assert s.joined is None and s.saved == []


def test_a_typo_can_be_corrected(situation, capsys):
    """A wrong password is retried, not fatal on the first attempt."""
    s = situation({A: "pit-lane"}, typed=["pit-lame", "pit-lane"])
    s.run()
    assert s.joined == A
    assert len(s.asked_for_password) == 2
    assert s.saved == [(A, "pit-lane")]
    assert "wrong password" in capsys.readouterr().err


def test_password_attempts_are_bounded(situation):
    """Never loop forever against an AP that will not let us in."""
    s = situation({A: "pit-lane"}, typed=["a", "b", "c", "d", "e"])
    with pytest.raises(OSError, match="after 3 attempts"):
        s.run()
    assert len(s.asked_for_password) == wifi.Transport.PASSWORD_TRIES


def test_a_changed_password_is_written_over_the_old_one(situation):
    """The logger's password was changed: the file must end up current."""
    s = situation({A: "new-pw"}, stored={A: "old-pw"}, typed=["new-pw"])
    s.run()
    assert s.attempts == [(A, "old-pw"), (A, "new-pw")]
    assert s.saved == [(A, "new-pw")]
    assert s.stored[A] == "new-pw"      # what a later run would load


def test_a_failed_retry_leaves_the_old_password_alone(situation):
    """Nothing worked, so nothing is known to be better than what is stored:
    deleting it would strand the watcher, which cannot be asked."""
    s = situation({A: "new-pw"}, stored={A: "old-pw"}, typed=["wrong", "also-wrong"])
    with pytest.raises(OSError):
        s.run()
    assert s.saved == []
    assert s.stored[A] == "old-pw"


def test_unattended_keeps_a_stale_password_for_a_human_to_fix(situation):
    """One flaky scan must not wipe a password the watcher still needs."""
    s = situation({A: "new-pw"}, stored={A: "old-pw"}, interactive=False)
    with pytest.raises(UserInputNeededError, match="interactively"):
        s.run()
    assert s.stored[A] == "old-pw"
    assert s.attempts == [(A, "old-pw")]


def test_no_loggers_in_range_is_a_plain_connection_failure(situation):
    s = situation({})
    with pytest.raises(OSError):
        s.run()
    assert s.attempts == []


def test_a_neighbours_network_is_not_a_logger(situation):
    s = situation({"Sissilandia": "housepw"})
    with pytest.raises(OSError):
        s.run()
    assert s.attempts == []            # never tried to join it


# ---------------------------------------------------- several loggers in range
def test_two_loggers_ask_which_one(situation):
    s = situation({A: OPEN, B: OPEN}, picks=["2"])
    s.run()
    assert s.joined == B
    assert s.asked_to_pick == 1


def test_choosing_the_open_one_skips_the_password(situation):
    """Mixed range: the choice decides whether a password is needed at all."""
    s = situation({A: OPEN, B: "spare-pw"}, picks=["1"])
    s.run()
    assert s.joined == A
    assert s.asked_for_password == []


def test_choosing_the_protected_one_asks_for_its_password(situation):
    s = situation({A: OPEN, B: "spare-pw"}, picks=["2"], typed=["spare-pw"])
    s.run()
    assert s.joined == B
    assert len(s.asked_for_password) == 1
    assert B in s.asked_for_password[0]
    assert s.saved == [(B, "spare-pw")]


def test_each_protected_logger_has_its_own_password(situation):
    """A password stored for one logger must not be offered for another."""
    s = situation({A: "a-pw", B: "b-pw"}, stored={A: "a-pw"},
                  picks=["2"], typed=["b-pw"])
    s.run()
    assert s.attempts == [(B, "b-pw")]
    assert s.saved == [(B, "b-pw")]


def test_two_protected_loggers_ask_only_about_the_chosen_one(situation):
    """Both need passwords; only the one picked is asked about."""
    s = situation({A: "a-pw", B: "b-pw"}, picks=["1"], typed=["a-pw"])
    s.run()
    assert s.joined == A
    assert len(s.asked_for_password) == 1
    assert A in s.asked_for_password[0] and B not in s.asked_for_password[0]
    assert s.saved == [(A, "a-pw")]


def test_picking_the_already_known_logger_asks_nothing(situation):
    """Two protected loggers, one already in the store: pick it, sail through."""
    s = situation({A: "a-pw", B: "b-pw"}, stored={B: "b-pw"}, picks=["2"])
    s.run()
    assert s.joined == B
    assert s.asked_for_password == []
    assert s.saved == []


def test_a_bad_password_does_not_fall_through_to_the_other_logger(situation):
    """Getting the chosen logger's password wrong is an error, not a retry
    against the one that was not chosen."""
    s = situation({A: "a-pw", B: "b-pw"}, picks=["1"],
                  typed=["wrong", "still-wrong", "nope"])
    with pytest.raises(OSError, match="wrong password"):
        s.run()
    # Every retry stayed on the chosen logger; B was never touched.
    assert {ssid for ssid, _pw in s.attempts} == {A}
    assert s.joined is None and s.saved == []


def test_the_choice_is_asked_again_every_time(situation):
    """Which loggers are in earshot changes; a remembered pick would be wrong."""
    s = situation({A: OPEN, B: OPEN}, picks=["1"])
    s.run()
    s.joined = None                    # walked out of range and came back
    s._picks = iter(["2"])
    s.run()
    assert s.asked_to_pick == 2
    assert s.joined == B


def test_choice_reprompts_until_valid(situation, capsys):
    s = situation({A: OPEN, B: OPEN}, picks=["", "9", "x", "2"])
    s.run()
    assert s.joined == B
    assert s.asked_to_pick == 4
    assert "between 1 and 2" in capsys.readouterr().err


def test_prompts_stay_off_stdout(situation, capsys):
    """stdout carries --json output; nothing conversational may land there."""
    s = situation({A: OPEN, B: OPEN}, picks=["1"])
    s.run()
    out = capsys.readouterr()
    assert B in out.err and "which one?" in out.err
    assert out.out == ""


def test_abandoned_choice_is_not_a_crash(situation, monkeypatch):
    s = situation({A: OPEN, B: OPEN})

    def ctrl_c(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", ctrl_c)
    with pytest.raises(UserInputNeededError):
        s.run()
    assert s.joined is None


# ------------------------------------------------------- unattended (watcher)
def test_unattended_single_open_logger_just_works(situation):
    s = situation({A: OPEN}, interactive=False)
    s.run()
    assert s.joined == A


def test_unattended_uses_a_stored_password(situation):
    """Once entered by hand, the watcher can use it forever."""
    s = situation({A: "pit-lane"}, stored={A: "pit-lane"}, interactive=False)
    s.run()
    assert s.joined == A


def test_unattended_will_not_guess_between_loggers(situation):
    s = situation({A: OPEN, B: OPEN}, interactive=False)
    with pytest.raises(UserInputNeededError) as exc:
        s.run()
    assert A in str(exc.value) and B in str(exc.value)
    assert s.joined is None            # nothing picked behind the user's back


def test_unattended_reports_a_missing_password(situation):
    s = situation({A: "pit-lane"}, interactive=False)
    with pytest.raises(UserInputNeededError, match="interactively"):
        s.run()
    assert s.joined is None


# ------------------------------------------------- through the CLI entrypoints
CSV = "name,size,laps,best\r\na_0186.xrz,1024,12,52123\r\n"


@pytest.fixture
def listing(monkeypatch, cfg, tmp_path):
    """`mt telemetry list remote` over WiFi, against the fake world."""
    monkeypatch.setattr(wifi.Transport, "session_csv", lambda _self: CSV)
    cfg.telemetry.data_dirs = [tmp_path / "downloads"]
    cfg.telemetry.transport = "wifi"     # skip the USB probe
    return cfg


def test_list_remote_asks_for_an_unknown_password(situation, listing):
    """Listing connects too, so it must prompt exactly like `get` does."""
    from media_tools.ingest.aim import list_remote_sessions

    s = situation({A: "pit-lane"}, typed=["pit-lane"])
    result = list_remote_sessions(listing)
    assert len(s.asked_for_password) == 1
    assert s.saved == [(A, "pit-lane")]
    assert result.transport == "WiFi"
    assert [x.name for x in result.sessions] == ["a_0186.xrz"]


def test_list_remote_asks_which_logger(situation, listing):
    from media_tools.ingest.aim import list_remote_sessions

    s = situation({A: OPEN, B: "spare-pw"}, picks=["2"], typed=["spare-pw"])
    result = list_remote_sessions(listing)
    assert s.joined == B
    assert s.asked_to_pick == 1 and len(s.asked_for_password) == 1
    assert result.transport == "WiFi"


def test_list_remote_reports_an_unanswerable_prompt(situation, listing):
    """Unattended, with two loggers up: a note, not a traceback."""
    from media_tools.ingest.aim import list_remote_sessions

    situation({A: OPEN, B: OPEN}, interactive=False)
    result = list_remote_sessions(listing)
    assert result.sessions == [] and result.transport is None
    assert result.notes and result.notes[0].startswith("!")
    assert "interactively" in result.notes[0]


# ------------------------------------------------------------------ surfacing
def test_connect_lets_the_ask_through(monkeypatch):
    """A logger needing input must not be reported as 'No MyChron found.'"""
    def usb():
        raise OSError("no usb")

    def needs_input():
        raise UserInputNeededError("2 AiM loggers in range")

    monkeypatch.setitem(TRANSPORTS, "usb", usb)
    monkeypatch.setitem(TRANSPORTS, "wifi", needs_input)
    with pytest.raises(UserInputNeededError, match="2 AiM loggers"):
        connect(verbose=False)


def test_connect_still_reports_a_plain_absence(monkeypatch):
    monkeypatch.setitem(TRANSPORTS, "usb", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setitem(TRANSPORTS, "wifi", lambda: (_ for _ in ()).throw(TimeoutError()))
    from media_tools.aim import NoDeviceError

    with pytest.raises(NoDeviceError, match="No MyChron found"):
        connect(verbose=False)
