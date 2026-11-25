# Troca Local

Aplicação de código aberto sob licença MIT para transferir arquivos entre celular e notebook por QR code. No modo provisório gratuito, o servidor roda no próprio notebook e guarda os arquivos localmente; não exige hospedagem nem armazenamento em nuvem.

## Requisitos

- Python 3.10 ou mais recente.
- `qrencode` para desenhar o QR code.
- Para transferência entre redes diferentes: `cloudflared` instalado no notebook e acesso à internet.

No Arch Linux, instale o gerador de QR com:

```sh
sudo pacman -S qrencode
```

Se `cloudflared` não estiver instalado, a versão local ainda funciona na mesma rede Wi-Fi. Para acesso externo, instale o Cloudflare Tunnel no notebook seguindo a documentação oficial: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/. O celular e os demais aparelhos usam apenas o navegador; não precisam instalar o aplicativo.

## Iniciar na mesma rede Wi-Fi

No terminal, dentro desta pasta:

```sh
./start-local.sh
```

Abra no navegador do notebook o endereço `Painel deste notebook` impresso no terminal. Mostre o QR code e escaneie com a câmera do celular. Para funcionar sem túnel, os dois aparelhos precisam estar na mesma rede local.

## Acessar de outra rede

No notebook Arch Linux, instale o cliente do túnel uma vez:

```sh
sudo pacman -S cloudflared
```

Em outras distribuições, siga a documentação oficial de instalação. Isso não ativa o R2 nem exige uma assinatura de armazenamento; o túnel temporário encaminha as conexões ao notebook. No celular, basta abrir o QR code no navegador.

Com `cloudflared` instalado, execute:

```sh
./start-remoto.sh
```

O painel atualiza o QR quando o túnel gratuito do Cloudflare fica pronto. Escaneie o novo código. O endereço temporário muda quando o programa é encerrado e iniciado novamente. O computador precisa permanecer ligado, conectado à internet e sem suspensão durante a transferência.

## Como transferir

- Para mandar do celular ao notebook: escaneie o QR, escolha os arquivos e toque em “Enviar arquivos”.
- Para mandar do notebook ao celular: abra no notebook o link da sessão mostrado no painel, envie o arquivo por essa página e baixe-o no celular.
- Qualquer aparelho com o link da sessão pode enviar e baixar arquivos. Trate o link e o QR como uma chave privada; não os publique.

## Limites e armazenamento

- Até 50 MB por envio e 10 arquivos selecionados por vez.
- Até 1 GB ocupado na pasta de transferências.
- Arquivos expiram após 24 horas e são removidos na próxima verificação automática.
- Os arquivos ficam na pasta `received/` deste projeto. A lista do aplicativo é local e uma sessão nova é criada sempre que o programa inicia.
- O túnel usa HTTPS para o acesso externo. Sem túnel, a conexão local usa HTTP; use apenas uma rede confiável.

## Encerrar

Pressione `Ctrl+C` no terminal. Isso encerra o servidor e o túnel. Arquivos já recebidos continuam na pasta até expirarem ou serem apagados manualmente.

Este projeto foi pensado para uso pessoal e temporário, não para hospedar um serviço público permanente. Não encaminhe portas do roteador para a internet.

## Testes

```sh
python3 -m unittest discover -s tests -v
```

## Publicar na nuvem (opcional, quando quiser)

A versão web usa Cloudflare Workers e R2 para funcionar sem manter o notebook ligado. Essa opção depende de ativar o R2 na conta e seguir a cobrança por uso; não é necessária para o modo gratuito acima. Não habilite uma assinatura se o painel mostrar uma cobrança que você não quer assumir.

### Publicação inicial

Requisitos: conta Cloudflare e Node.js/npm instalados no computador usado para publicar.

```sh
cd /caminho/para/qr-transfer
npm ci
npx wrangler login
npx wrangler r2 bucket create troca-local-files
npm run deploy
```

`wrangler login` abre o navegador para autorizar a conta. A publicação apresenta o endereço `workers.dev` do site. Se o nome do bucket já existir na conta, escolha outro nome e atualize `bucket_name` em `wrangler.jsonc` antes de publicar.

Para testar localmente antes de publicar:

```sh
npm run dev
```

A limpeza automática roda de hora em hora. Links de sessão expiram após 24 horas; cada arquivo pode ter até 50 MB, são aceitos até 10 arquivos por envio e cada sessão tem limite de 1 GB.

### Custos e privacidade

O armazenamento R2 tem cotas gratuitas mensais, mas não é ilimitado. Confira uso e faturamento no painel Cloudflare; ultrapassar as cotas pode gerar cobrança. Configure limites de gastos na conta antes de divulgar o site.

O QR code e o link funcionam como uma chave: qualquer pessoa que os tiver pode enviar e baixar arquivos daquela sessão. Não publique links de sessão. O site é destinado a uso pessoal e temporário, não a hospedagem pública sem moderação.

Documentação oficial: https://developers.cloudflare.com/pages/functions/bindings/ e https://developers.cloudflare.com/r2/pricing/.

## Licença

Este projeto é distribuído sob a licença MIT. Consulte o arquivo `LICENSE`.


