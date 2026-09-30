# events — one calendar of the upcoming events from the Luma calendars you follow, your Partiful
# events and AGI House, with vibe and topic filters. The program, its package, the generic module
# (services.events, module.nix) and a standalone flake sit beside this file; this half is a lab's
# wiring around them: an endpoint in the address book, a vhost behind the door and the database on
# persistent storage. Evaluated only inside agent-lab (public/modules/events/).
#
# The app has no login of its own and keeps personal credentials in its database (a Luma session,
# Partiful tokens, private calendar links), so every request passes the portal sign-in and the door
# admits the admins alone unless lab.proxy.apps.events.groups adds a group.
#
#   lab.events.enable          run it
#   lab.events.port            its loopback port
#   lab.events.dataDir         the database; bind-mounted onto the unit's state dir
#   lab.events.lumaCalendars   Luma calendars to follow without signing in to Luma
#   lab.events.agihouse        include AGI House's public events
{
  config,
  lib,
  ...
}:
let
  cfg = config.lab.events;
  ep = config.lab.net.endpoints.events;
in
{
  imports = [ ./module.nix ];

  options.lab.events = {
    enable = lib.mkEnableOption "events, one calendar of the Luma, Partiful and AGI House events you follow";
    port = lib.mkOption {
      type = lib.types.port;
      default = 18104;
      description = "The loopback port events binds.";
    };
    dataDir = lib.mkOption {
      type = lib.types.str;
      default = "/persist/events";
      description = "Persistent directory for the database; bind-mounted onto the unit's state dir.";
    };
    lumaCalendars = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "https://luma.com/genai-sf"
        "cal-XXXXXXXXXXXXXXX"
      ];
      description = "Luma calendars to follow without signing in to Luma: calendar links, slugs or cal- ids.";
    };
    agihouse = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Include AGI House's public events.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion =
          config.lab.proxy.authEndpoint != "events"
          && lib.all (
            app:
            app.endpoint != "events"
            || (app.bypassPaths == [ ] && app.bypassNetworks == [ ] && app.plainPort == null)
          ) (lib.attrValues config.lab.proxy.apps);
        message = "events has no login of its own and keeps personal credentials, so its proxy entries must require sign-in.";
      }
    ];

    lab.net.endpoints.events = {
      address = "127.0.0.1";
      inherit (cfg) port;
    };
    lab.proxy.apps.events.title = "Events — what's on from the calendars you follow";

    services.events = {
      enable = true;
      inherit (ep) address port;
      inherit (cfg) lumaCalendars agihouse;
    };

    # The events account owns the database; the directory is bound over the unit's state dir.
    lab.state.events = {
      path = cfg.dataDir;
      bindTo = "/var/lib/events";
      owner = "events";
      group = "events";
      mode = "0700";
      units = [ "events.service" ];
      description = "the event database, preferences and the Luma and Partiful credentials connected in the app";
    };
  };
}
