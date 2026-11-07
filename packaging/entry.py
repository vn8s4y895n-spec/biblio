"""Point d'entrée de l'application empaquetée : sans argument (ou avec des paquets .biblio), la fenêtre ;
avec une commande (`serve`, `mcp`…), la CLI. Claude Desktop lance ainsi `Biblio mcp`."""

import sys

from biblio import main
from biblio.app import run

args = sys.argv[1:]
if all(a.lower().endswith(".biblio") for a in args):
    run(paths=args)
else:
    main()
