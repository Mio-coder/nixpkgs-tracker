{ config, lib, pkgs, ... }:

let
  cfg = config.services.nixpkgs-pr-tracker;
  stateDir = "/var/lib/nixpkgs-pr-tracker";
  scheduleLabel =
    if cfg.refreshCalendar == "*-*-* 00,06,12,18:00:00" then "Every 6 hours"
    else if cfg.refreshCalendar == "daily" then "Daily"
    else "Custom schedule";
  runtimeConfig = pkgs.writeText "nixpkgs-pr-tracker.json" (builtins.toJSON {
    inherit (cfg) availableTargets initialTargets;
  });
  args = lib.escapeShellArgs [
    "--state-dir" stateDir
    "--config" (toString runtimeConfig)
  ] + lib.optionalString (cfg.tokenFile != null) " --token-file %d/github-token";
  commonService = {
    User = "nixpkgs-pr-tracker";
    Group = "nixpkgs-pr-tracker";
    StateDirectory = "nixpkgs-pr-tracker";
    StateDirectoryMode = "0700";
    UMask = "0077";
    LoadCredential = lib.optionals (cfg.tokenFile != null) [ "github-token:${cfg.tokenFile}" ];
    NoNewPrivileges = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
    RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
    LockPersonality = true;
    CapabilityBoundingSet = "";
  };
in {
  options.services.nixpkgs-pr-tracker = {
    enable = lib.mkEnableOption "the local Nixpkgs PR watchlist";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "Tracker package to run.";
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 8732;
      description = "HTTP port, bound only to 127.0.0.1.";
    };
    availableTargets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "staging" "staging-next" "master" "nixos-unstable-small" "nixpkgs-unstable" "nixos-unstable" ];
      example = [ "master" "nixos-unstable" "nixos-26.05" "nixos-26.05-small" ];
      description = "Branches offered in the dashboard checklist. Add explicit stable release branches here.";
    };
    initialTargets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "nixos-unstable" ];
      description = "Initial selection for a fresh database, or fallback when every saved target is removed from availableTargets. Subsequent dashboard selections persist.";
    };
    refreshCalendar = lib.mkOption {
      type = lib.types.str;
      default = "*-*-* 00,06,12,18:00:00";
      example = "daily";
      description = "systemd OnCalendar schedule for checks while the browser is closed.";
    };
    tokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/var/lib/secrets/nixpkgs-pr-tracker-token";
      description = "Absolute runtime path to a GitHub token file, read through systemd credentials. Use a quoted string, never a Nix path literal or token contents.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.availableTargets != [ ] && cfg.initialTargets != [ ]
          && lib.all (target: builtins.elem target cfg.availableTargets) cfg.initialTargets;
        message = "nixpkgs-pr-tracker: availableTargets and initialTargets must be nonempty, and all initialTargets must be available.";
      }
      {
        assertion = cfg.tokenFile == null || (lib.hasPrefix "/" cfg.tokenFile && !(lib.hasPrefix "/nix/store/" cfg.tokenFile));
        message = "nixpkgs-pr-tracker: tokenFile must be an absolute runtime path outside /nix/store.";
      }
    ];
    users.groups.nixpkgs-pr-tracker = { };
    users.users.nixpkgs-pr-tracker = {
      isSystemUser = true;
      group = "nixpkgs-pr-tracker";
    };
    systemd.services.nixpkgs-pr-tracker = {
      description = "Local Nixpkgs PR watchlist";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      serviceConfig = commonService // {
        ExecStart = "${lib.getExe cfg.package} serve ${args} --port ${toString cfg.port} --schedule-label ${lib.escapeShellArg scheduleLabel}";
        Restart = "on-failure";
        RestartSec = 5;
      };
    };
    systemd.services.nixpkgs-pr-tracker-refresh = {
      description = "Refresh starred Nixpkgs PRs";
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      serviceConfig = commonService // {
        Type = "oneshot";
        ExecStart = "${lib.getExe cfg.package} refresh ${args}";
        # Checks are paced and each network request has a timeout. Large
        # watchlists must not be killed by systemd's short oneshot default.
        TimeoutStartSec = "1h";
      };
    };
    systemd.timers.nixpkgs-pr-tracker-refresh = {
      description = "Occasional Nixpkgs PR checks";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.refreshCalendar;
        RandomizedDelaySec = "5m";
        Persistent = true;
      };
    };
  };
}
