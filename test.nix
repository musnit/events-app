# The lab's events on one box: from empty state the app is ready behind the door with no one
# setting it up. The configured Luma calendar is followed, the database lives on persistent storage
# where only the unit can read it, every request needs the portal sign-in, and the app's own writes
# work through the door. A restart keeps what was chosen in the app. The VM has no internet, so
# pulls fail and back off; a stand-in answers the door's questions the way the portal does.
{ ... }:
let
  # The portal's authz contract: 401 with the sign-in address unless the request carries
  # X-Test-Auth, then 200 with the signed-in identity.
  stubPortal =
    pkgs:
    pkgs.writeText "stub-portal.py" ''
      from http.server import BaseHTTPRequestHandler, HTTPServer
      from urllib.parse import quote
      class H(BaseHTTPRequestHandler):
          def do_GET(self):
              if self.headers.get("X-Test-Auth") == "yes":
                  self.send_response(200)
                  self.send_header("Remote-User", "owner")
                  self.send_header("Remote-Groups", "admins")
              else:
                  self.send_response(401)
                  self.send_header("Location", "https://auth.box.example.test/?rd=" + quote(self.headers.get("X-Original-URL", ""), safe=""))
              self.send_header("Content-Length", "0")
              self.end_headers()
          def log_message(self, *a):
              pass
      HTTPServer(("127.0.0.1", 19091), H).serve_forever()
    '';
in
{
  name = "events";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [
        ../lib/box-under-test.nix
        ./.
        ../proxy
        ../www.nix
        ../domain
      ];
      lab.domain.name = "box.example.test";
      lab.owner = {
        user = "owner";
        email = "owner@example.test";
      };
      lab.net.endpoints.authelia = {
        address = "127.0.0.1";
        port = 19091;
      };
      lab.events = {
        enable = true;
        lumaCalendars = [ "cal-TestCalendar01" ];
      };
      systemd.services.stub-portal = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${stubPortal pkgs}";
      };
      # an ordinary login on the box, which must not read the database either
      users.users.someone.isNormalUser = true;
      environment.systemPackages = [ pkgs.curl ];
    };

  testScript =
    { nodes, ... }:
    let
      ep = nodes.machine.lab.net.endpoints.events;
    in
    ''
      import json
      import shlex

      url = "http://${ep.address}:${toString ep.port}"
      site = "https://events.box.example.test"
      door = "curl -sS --cacert /var/lib/lab-ca/ca.pem --resolve events.box.example.test:443:127.0.0.1"
      configured = "cal-TestCalendar01"

      def through_door(path, *args):
          return machine.succeed(" ".join([door, "-H 'X-Test-Auth: yes'", *args, shlex.quote(site + path)]))

      machine.wait_for_unit("events.service")
      machine.wait_for_unit("nginx.service")
      machine.wait_for_open_port(${toString ep.port})
      machine.wait_for_open_port(19091)

      with subtest("the app is ready from empty state and follows the configured calendar"):
          assert json.loads(machine.succeed(f"curl -fsS {url}/api/health"))["ok"]
          machine.wait_until_succeeds(f"curl -fsS {url}/api/events | grep -q {configured}", timeout=120)

      with subtest("the database is on persistent storage, readable by the unit alone"):
          machine.succeed("test -s /persist/events/events.db")
          machine.succeed("findmnt -n /var/lib/events")
          assert machine.succeed("stat -c '%a %U' /persist/events /persist/events/events.db").split() == ["700", "events", "600", "events"]
          for user in ["nobody", "someone"]:
              for path in ["/persist/events/events.db", "/var/lib/events/events.db"]:
                  machine.fail(f"runuser -u {user} -- cat {path}")

      with subtest("every request needs the portal sign-in"):
          for path in ["/", "/api/events", "/api/status", "/feed.ics"]:
              out = machine.succeed(f"{door} -o /dev/null -w '%{{http_code}} %{{redirect_url}}' {site}{path}")
              assert out.startswith("302 https://auth.box.example.test/"), (path, out)
          out = machine.succeed(f"{door} -o /dev/null -w '%{{http_code}}' -X PUT -H 'Content-Type: application/json' --data '{{}}' {site}/api/prefs")
          assert out.strip() == "302", out

      with subtest("signed in, the app and its writes work through the door"):
          assert "<title>Events</title>" in through_door("/month/2026-10")
          # a browser's same-origin write, and one from an older browser that sends Origin alone
          for headers in ["-H 'Sec-Fetch-Site: same-origin'", f"-H 'Origin: {site}'"]:
              out = through_door("/api/prefs", "-X PUT", headers, "-H 'Content-Type: application/json'",
                                 "-o /dev/null -w '%{http_code}'", "--data '{\"area\": \"all\"}'")
              assert out.strip() == "200", (headers, out)
          out = through_door("/api/prefs", "-X PUT", "-H 'Sec-Fetch-Site: cross-site'", "-H 'Content-Type: application/json'",
                             "-o /dev/null -w '%{http_code}'", "--data '{\"area\": \"bay\"}'")
          assert out.strip() == "403", out

      with subtest("a restart keeps the choices made in the app and repeats no setup"):
          through_door("/api/prefs", "-X PUT", "-H 'Sec-Fetch-Site: same-origin'", "-H 'Content-Type: application/json'",
                       f"--data '{{\"muted_calendars\": [\"{configured}\"]}}'")
          machine.succeed("systemctl restart events.service")
          machine.wait_for_open_port(${toString ep.port})
          status = json.loads(machine.succeed(f"curl -fsS {url}/api/status"))
          assert status["prefs"] == {"area": "all", "muted_calendars": [configured]}, status["prefs"]
          assert status["luma"]["calendars_by_origin"]["config"] == 1, status["luma"]
    '';
}
