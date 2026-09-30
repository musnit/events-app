# This NixOS module declares services.events and knows nothing about any particular lab. It turns
# the package into a sandboxed systemd unit and translates options into the EVENTS_* environment
# the program reads. A lab sets these options from its own module (default.nix).
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.events;
  flag = value: if value then "1" else "0";
in
{
  options.services.events = {
    enable = lib.mkEnableOption "events, one calendar of the Luma, Partiful and AGI House events you follow";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "The events package to run.";
    };

    address = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = ''
        Address to bind. The app has no sign-in of its own, so keep it on loopback behind a
        reverse proxy that authenticates every request.
      '';
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8771;
      description = "Port to bind.";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/events";
      description = ''
        The database: events, calendars, preferences, and the Luma session, Partiful tokens and
        private feed links you connect. The events account owns it (systemd StateDirectory).
      '';
    };

    lumaCalendars = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "https://luma.com/genai-sf"
        "cal-XXXXXXXXXXXXXXX"
      ];
      description = ''
        Luma calendars to follow without signing in to Luma: calendar links, slugs or cal- ids.
        They sit beside the calendars you import from your Luma account, and the app cannot
        remove them.
      '';
    };

    agihouse = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Include AGI House's public event list.";
    };

    sync = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Pull the sources in the background. Off serves only what the database already holds.";
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = {
        EVENTS_LUMA_INTERVAL = "7200";
      };
      description = "Further EVENTS_* settings, such as pull intervals (see README.md).";
    };
  };

  config = lib.mkIf cfg.enable {
    # A real account owns the database. A DynamicUser's state sits on disk owned by nobody behind
    # an id-mapped mount, and a copy of it reachable elsewhere (a bind-mount source) would be
    # readable by anything running as nobody.
    users.users.events = {
      isSystemUser = true;
      group = "events";
    };
    users.groups.events = { };

    systemd.services.events = {
      description = "Gather the events you follow into one calendar";
      wantedBy = [ "multi-user.target" ];
      # The first pulls start at once; without the network they would wait out a backoff.
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];

      environment = {
        EVENTS_LISTEN = "${cfg.address}:${toString cfg.port}";
        EVENTS_STATE_DIR = cfg.stateDir;
        EVENTS_LUMA_CALENDARS = lib.concatStringsSep " " cfg.lumaCalendars;
        EVENTS_AGIHOUSE = flag cfg.agihouse;
        EVENTS_SYNC = flag cfg.sync;
      }
      // cfg.environment;

      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        Restart = "on-failure";
        RestartSec = "5s";

        # Sessions and tokens live in the state dir, so only the account reads it.
        User = "events";
        Group = "events";
        StateDirectory = builtins.baseNameOf cfg.stateDir;
        StateDirectoryMode = "0700";
        UMask = "0077";

        # The sandbox lets it read the OS, write its state dir and open sockets (AF_UNIX for name lookups).
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
        ProtectProc = "invisible";
        ProcSubset = "pid";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        SystemCallArchitectures = "native";
        SystemCallFilter = [
          "@system-service"
          "~@privileged"
        ];
      };
    };

    assertions = [
      {
        assertion = dirOf cfg.stateDir == "/var/lib";
        message = "services.events.stateDir must be a direct child of /var/lib (systemd StateDirectory)";
      }
    ];
  };
}
