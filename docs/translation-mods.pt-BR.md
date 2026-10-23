# Mods de tradução e dublagem

Traduções feitas por fãs (texto e, às vezes, voz) entram como mods de RomFS do jogador (veja "Mods" no README). Esta
página conta o que foi preciso para rodar uma no Mac: um mod de texto e voz em português do Brasil para a 1.2.1, com 1.410
arquivos, num MacBook Air M1 (19/10/2026). Nada aqui distribui conteúdo do jogo; baixe o mod de quem o fez.

## 1. O mod precisa ser da mesma versão do app

Cada tradução é feita para uma versão do jogo, e o nome dos arquivos diz qual. Na RomFS, `Mals/<idioma>.Product.<n>.sarc.zs`
guarda o texto e `System/Resource/ResourceSizeTable.Product.<n>.rsizetable.zs` a tabela de tamanhos: `100` é a 1.0.0, `121`
é a 1.2.1 (a 1.2.1 mantém os arquivos de interface como `Common.Product.110` e `Title.Product.110`), e a 1.4.3 tem
`Mals/*.Product.142`, a tabela `Product.143` e `Common.Product.140`. Um mod da 1.2.1 num app
1.0.0 não muda o texto (a 1.0.0 lê `Product.100`), e as vozes junto com a tabela de outra versão podem não funcionar. Gere o
app na versão do mod (`--update` com esse update) e coloque o mod em `mods/<essa versão>`, por exemplo `mods/v121`.

## 2. Instalar

Copie o conteúdo da pasta `romfs` do mod (as pastas `Mals`, `UI`, `Voice`, `Sound`, ...) para a pasta de mods e gere o
manifest:

```
python3 games/totk/scripts/make_mod_manifest.py ~/Library/Application\ Support/MacNativeGames/TOTK/mods/v121 \
    --app "Tears of the Kingdom.app"
```

Arquivos que a RomFS não tem entram como novos; um caminho que só difere do jogo em maiúsculas e minúsculas
faz a ferramenta parar. Alguns mods trazem também uma cópia sem compressão de um arquivo ao lado do `.zs` (por exemplo
`Common.Product.110.Nin_NX_NVN.blarc`). O jogo lê o `.zs`; a cópia extra só entraria como arquivo novo, então pode ficar de fora.

## 3. Escolher o idioma que o mod substitui

Em geral a tradução substitui um dos idiomas do jogo, quase sempre o inglês americano (`USen`: texto em `Mals/USen...`,
voz em `Voice/Resource/USen`). O jogo segue o idioma do Mac, então, se o Mac não estiver no idioma que o mod substitui, o
texto do mod não aparece. Deixe só este jogo no idioma substituído:

```
defaults write org.macnativegames.totk AppleLanguages -array en-US
```

`defaults delete org.macnativegames.totk AppleLanguages` volta para o idioma do Mac. Abrindo pelo Terminal,
`TOTK_LANG=en-US` faz o mesmo numa execução.

## 4. O arquivo de interface e o patch do próprio build

A maioria das traduções também substitui `UI/LayoutArchive/Common.Product.<n>.Nin_NX_NVN.blarc.zs` (as texturas da
interface que têm palavras). O patch de RomFS do build (`make_romfs_patches.py`, o momento da captura do menu rápido)
mexe no mesmo arquivo, e dois overlays não podem substituir o mesmo arquivo. O `make_mod_manifest.py` resolve isso (#73):
quando o mod tem esse arquivo, ele coloca a mudança do build dentro da cópia do mod, guarda o arquivo original do mod ao
lado como `<nome>.mod-original` (usado de novo na próxima vez) e avisa:

```
merged with the build's patch: UI/LayoutArchive/Common.Product.110.Nin_NX_NVN.blarc.zs (the mod's own file: ...mod-original)
```

Ao abrir, o log mostra que o arquivo do mod entra no lugar do arquivo do build:

```
RomFS overlay: UI/LayoutArchive/Common.Product.110.Nin_NX_NVN.blarc.zs (6275634 bytes) replaces the build's overlay extracted/v121/romfs-patches/... (the mod holds its edit)
```

Um mod que mudou ele mesmo os membros do patch é recusado, e nada é gravado. Se um build futuro mudar o patch, rode o
`make_mod_manifest.py` de novo (o jogo diz ao abrir qual arquivo não confere mais).

## 5. Conferir

O log vem desligado: ligue com `defaults write org.macnativegames.totk TOTK_DEBUG_LOG -bool YES` e abra o jogo de novo. O
log (`~/Library/Logs/MacNativeGames/totk.log`) então mostra o mod ao abrir:

```
RomFS overlay: 1410 files from .../mods/v121 (1394 more not listed)
RomFS overlay: .../mods/v121 checked, 1410 files, 144.5 MB hashed in 0.69 s
```

Se um hash não bater, o jogo para ao abrir e o log diz qual arquivo, então um mod alterado nunca carrega pela metade. Um mod
de outra versão não para o jogo: os arquivos entram como arquivos novos que o jogo não lê, então ele simplesmente não tem efeito.
