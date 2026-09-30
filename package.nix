# This package builds the server (Python, standard library only) and the web app it serves offline
# from this directory, and runs both test suites. `nix build` builds it here; a NixOS module or a lab
# imports it by path with pkgs.callPackage.
{
  lib,
  stdenvNoCC,
  buildNpmPackage,
  python3,
  makeWrapper,
}:
let
  version = "2.1.0";

  # The web app is type-checked, unit-tested and bundled from the locked npm dependencies.
  web = buildNpmPackage {
    pname = "events-web";
    inherit version;
    src = lib.fileset.toSource {
      root = ./web;
      fileset = lib.fileset.difference ./web (
        lib.fileset.unions [
          (lib.fileset.maybeMissing ./web/node_modules)
          (lib.fileset.maybeMissing ./web/dist)
        ]
      );
    };
    npmDepsHash = "sha256-Wv7mAD+mZZH9C3RtFTLCHB3gi1Y62hVzhSzoL0bVu6I=";
    npmRebuildFlags = [ "--ignore-scripts" ];
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      npm test
      runHook postCheck
    '';
    installPhase = ''
      runHook preInstall
      cp -r dist "$out"
      runHook postInstall
    '';
  };
in
stdenvNoCC.mkDerivation {
  pname = "events";
  inherit version;
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      (lib.fileset.fileFilter (file: file.hasExt "py") ./events)
      (lib.fileset.fileFilter (file: !file.hasExt "pyc") ./tests)
    ];
  };
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 ${python3}/bin/python3 -W error::ResourceWarning -m unittest discover -s tests -t .
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p $out/lib $out/bin
    cp -r events $out/lib/
    ${python3}/bin/python3 -m compileall -q $out/lib/events
    # -P keeps Python from importing the working directory, which may hold a checkout's copy.
    makeWrapper ${python3}/bin/python3 $out/bin/events \
      --add-flags "-P -m events" \
      --set PYTHONPATH $out/lib \
      --set PYTHONDONTWRITEBYTECODE 1 \
      --set-default EVENTS_WEB_DIR ${web}
    runHook postInstall
  '';
  passthru = { inherit web; };
  meta = {
    description = "One calendar of the upcoming events from the Luma calendars you follow, Partiful and AGI House";
    homepage = "https://github.com/musnit/events-app";
    mainProgram = "events";
    platforms = lib.platforms.unix;
  };
}
