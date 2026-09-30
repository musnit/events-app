# Standalone flake, so the app runs anywhere with Nix:
#
#   nix run .                     # serves on 127.0.0.1:8771, state in ~/.local/state/events
#   nix build .                   # the package; its build runs both test suites
#   nix flake check               # the package and a VM test of the NixOS module
#   imports = [ inputs.events.nixosModules.default ]; services.events.enable = true;
#
# A lab does not go through this file: it imports package.nix and module.nix by path
# (default.nix) so they share the lab's nixpkgs pin.
{
  description = "events: one calendar of the Luma, Partiful and AGI House events you follow";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        default = events;
        events = pkgs.callPackage ./package.nix { };
      });

      nixosModules.default = import ./module.nix;

      checks =
        nixpkgs.lib.genAttrs
          [
            "x86_64-linux"
            "aarch64-linux"
          ]
          (
            system:
            let
              pkgs = nixpkgs.legacyPackages.${system};
            in
            {
              events = self.packages.${system}.events;
              vm = pkgs.testers.runNixOSTest ./standalone-test.nix;
            }
          );

      formatter = forAll (pkgs: pkgs.nixfmt);
    };
}
