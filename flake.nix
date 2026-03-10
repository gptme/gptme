{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs";
    multiprocessing-logging = {
      url = "github:jruere/multiprocessing-logging";
      flake = false;
    };
  };

  outputs = inputs@{ self, nixpkgs, ... }:
  let
    forAllSystems = f:
      nixpkgs.lib.genAttrs
         [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ]
         (system: f nixpkgs.legacyPackages.${system});
  in {
    inherit (self) inputs;

    packages = forAllSystems (pkgs: {
      default = 
      let
        multiprocessing-logging = pkgs.python3Packages.buildPythonPackage {
          pname = "multiprocessing-logging";
          version = inputs.multiprocessing-logging.rev;
          src = inputs.multiprocessing-logging;
          pyproject = true;
          build-system = [ pkgs.python3Packages.setuptools ];
        };
      in pkgs.python3Packages.buildPythonApplication {
        name = "gptme";
        srcs = [
          ./gptme
          ./pyproject.toml
          ./README.md
        ];
        unpackPhase = ''
          for file in $srcs; do
            echo $file
            cp -r $file $(stripHash $file)
          done
        '';
        pyproject = true;
        build-system = [ pkgs.python3Packages.poetry-core ];
        nativeBuildInputs = [ pkgs.python3Packages.pythonRelaxDepsHook ];
        dependencies = with pkgs.python3Packages; [
          click
          click-default-group
          python-dotenv
          rich
          tabulate
          pick
          tiktoken
          tomlkit
          typing-extensions
          platformdirs
          lxml
          pypdf
          requests
          json-repair
          mcp
          questionary
          deprecated
          python-dateutil
          pyyaml
          openai
          anthropic
          ipython
          bashlex
          pillow
          multiprocessing-logging
          pydantic
          flask
          flask-cors
          playwright 
        ];
        pythonRelaxDeps = [
          "anthropic"
          "ipython"
          "json-repair"
          "openai"
          "pypdf"
          "rich"
        ];

      };
    });
  };
}
