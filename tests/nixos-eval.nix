{ nixpkgs, system ? "x86_64-linux" }:
let
  evaluated = import (nixpkgs + "/nixos/lib/eval-config.nix") {
    inherit system;
    modules = [
      ../nix/module.nix
      {
        system.stateVersion = "25.11";
        services.nixpkgs-pr-tracker = {
          enable = true;
          port = 9876;
          tokenFile = "/run/secrets/github-tracker";
          availableTargets = [ "master" "nixos-unstable" "nixos-26.05" ];
          initialTargets = [ "master" "nixos-26.05" ];
        };
      }
    ];
  };
  cfg = evaluated.config;
  web = cfg.systemd.services.nixpkgs-pr-tracker.serviceConfig;
  refresh = cfg.systemd.services.nixpkgs-pr-tracker-refresh.serviceConfig;
  timer = cfg.systemd.timers.nixpkgs-pr-tracker-refresh.timerConfig;
  lib = evaluated.pkgs.lib;
in
assert web.User == refresh.User;
assert web.StateDirectory == refresh.StateDirectory;
assert web.StateDirectoryMode == "0700";
assert web.LoadCredential == [ "github-token:/run/secrets/github-tracker" ];
assert web.LoadCredential == refresh.LoadCredential;
assert lib.hasInfix "--port 9876" web.ExecStart;
assert lib.hasInfix "--token-file %d/github-token" web.ExecStart;
assert lib.hasInfix "--token-file %d/github-token" refresh.ExecStart;
assert timer.Persistent;
assert timer.OnCalendar == "*-*-* 00,06,12,18:00:00";
assert timer.RandomizedDelaySec == "5m";
assert cfg.networking.firewall.allowedTCPPorts == [ ];
true
