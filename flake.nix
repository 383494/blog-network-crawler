{
  description = "pip env";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (p: [
              p.pip
              p.playwright 
              p.openai 
              p.beautifulsoup4 
              p.networkx 
              p.scipy 
              p.pyvis
            ]))
          ];
        };
      }
    );
}


