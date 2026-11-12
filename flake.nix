{
  description = "A local NixOS PR watchlist with favourites and quiet background checks";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in {
      packages = eachSystem (system: let pkgs = import nixpkgs { inherit system; }; in {
        default = pkgs.callPackage ./nix/package.nix { };
        nixpkgs-pr-tracker = self.packages.${system}.default;
      });
      apps = eachSystem (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/nixpkgs-pr-tracker";
        };
      });
      devShells = eachSystem (system: let pkgs = import nixpkgs { inherit system; }; in {
        default = pkgs.mkShell { packages = [ pkgs.python3 pkgs.nodejs ]; };
      });
      nixosModules.default = import ./nix/module.nix;
      checks = eachSystem (system: let pkgs = import nixpkgs { inherit system; }; in {
        package = self.packages.${system}.default;
        module = assert import ./tests/nixos-eval.nix { inherit nixpkgs system; };
          pkgs.runCommand "nixpkgs-pr-tracker-module-check" { } "touch $out";
      });
    };
}
