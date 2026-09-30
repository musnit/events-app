# This VM test checks the generic module alone (the flake's `vm` check). From empty state the
# sandboxed unit serves the app, follows the calendars named in its configuration without any help
# and keeps its state to itself, and a restart keeps what was chosen in the app. The VM has no
# internet, so every pull fails and backs off while the app keeps serving. The lab's own test is
# test.nix.
{ ... }:
{
  name = "events-module";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [ ./module.nix ];
      system.stateVersion = "26.05";
      services.events = {
        enable = true;
        port = 18771;
        lumaCalendars = [
          "cal-TestCalendar01"
          "https://luma.com/unreachable"
        ];
        agihouse = false;
      };
      environment.systemPackages = [ pkgs.curl ];
    };

  testScript = ''
    import json

    url = "http://127.0.0.1:18771"
    configured = "cal-TestCalendar01"

    def get(path):
        return json.loads(machine.succeed(f"curl -fsS {url}{path}"))

    def put(path, body):
        return json.loads(machine.succeed(
            f"curl -fsS -X PUT -H 'Content-Type: application/json' --data '{json.dumps(body)}' {url}{path}"
        ))

    def calendar(cal_id):
        return next((c for c in get("/api/events")["calendars"] if c["id"] == cal_id), None)

    machine.wait_for_unit("events.service")
    machine.wait_for_open_port(18771)

    with subtest("the app answers from empty state, on every route"):
        assert get("/api/health")["ok"]
        for path in ["/", "/month/2026-10", "/event/evt-missing", "/sources/luma"]:
            page = machine.succeed(f"curl -fsS {url}{path}")
            assert "<title>Events</title>" in page, (path, page)
        machine.succeed(f"curl -fsS {url}/manifest.webmanifest | grep -q standalone")
        machine.fail(f"curl -fsS {url}/api/nothing")

    with subtest("configured calendars are followed without the network"):
        # A cal- id is followed at once and named after its first good pull; a link waits for
        # Luma to resolve it, and its failure is on the Sources page.
        machine.wait_until_succeeds(f"curl -fsS {url}/api/events | grep -q {configured}", timeout=120)
        assert calendar(configured)["origins"] == ["config"], calendar(configured)
        feed = next(f for f in get("/api/status")["feeds"] if f["key"] == "luma:config")
        assert "unreachable" in (feed["last_error"] or ""), feed
        assert calendar("agihouse") is None

    with subtest("the state dir belongs to the unit alone"):
        machine.succeed("test -s /var/lib/events/events.db")
        assert machine.succeed("stat -c '%a %U' /var/lib/events /var/lib/events/events.db").split() == ["700", "events", "600", "events"]
        assert machine.succeed("ps -o user= -p $(systemctl show -p MainPID --value events.service)").strip() == "events"
        machine.fail("runuser -u nobody -- cat /var/lib/events/events.db")

    with subtest("a restart keeps the choices made in the app and repeats no setup"):
        put("/api/prefs", {"area": "all", "muted_calendars": [configured]})
        machine.succeed("systemctl restart events.service")
        machine.wait_for_open_port(18771)
        prefs = get("/api/status")["prefs"]
        assert prefs == {"area": "all", "muted_calendars": [configured]}, prefs
        assert calendar(configured)["muted"], calendar(configured)
        assert [c["id"] for c in get("/api/events")["calendars"]].count(configured) == 1
        # SIGTERM stops it cleanly rather than at the stop timeout.
        machine.succeed("journalctl -u events.service | grep -q 'events: stopped'")
  '';
}
