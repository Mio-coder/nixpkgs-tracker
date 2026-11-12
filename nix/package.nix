{ lib, stdenvNoCC, python3, nodejs, makeWrapper, cacert }:

stdenvNoCC.mkDerivation {
  pname = "nixpkgs-pr-tracker";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      (lib.fileset.fileFilter (file: file.hasExt "py" || file.hasExt "js" || file.hasExt "css" || file.hasExt "html") ../tracker)
      (lib.fileset.fileFilter (file: file.hasExt "py" || file.hasExt "js") ../tests)
    ];
  };

  nativeBuildInputs = [ python3 nodejs makeWrapper ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    python -m unittest discover -s tests -v
    node tests/test_ui.js
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/nixpkgs-pr-tracker" "$out/bin"
    cp -r tracker "$out/lib/nixpkgs-pr-tracker/"
    find "$out/lib" -name __pycache__ -type d -exec rm -rf {} +
    makeWrapper ${python3}/bin/python3 "$out/bin/nixpkgs-pr-tracker" \
      --add-flags "-m tracker" \
      --prefix PYTHONPATH : "$out/lib/nixpkgs-pr-tracker" \
      --set-default SSL_CERT_FILE "${cacert}/etc/ssl/certs/ca-bundle.crt" \
      --set PYTHONDONTWRITEBYTECODE 1
    runHook postInstall
  '';

  meta = {
    description = "Local Nixpkgs PR watchlist with occasional background checks";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "nixpkgs-pr-tracker";
  };
}
