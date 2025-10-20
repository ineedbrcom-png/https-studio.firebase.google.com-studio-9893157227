# Studio Backend

Este repositório contém um back-end completo, construído apenas com a biblioteca padrão do Python, para gerenciar projetos colaborativos de um estúdio criativo. Ele fornece recursos de autenticação, controle de acesso, gerenciamento de tarefas, ativos e registro de atividades, sem depender de pacotes externos (útil para ambientes sem acesso à internet).

## Principais recursos

- Registro e autenticação de usuários com senhas protegidas por PBKDF2 e emissão de tokens JWT (HS256).
- Gestão completa de projetos: criação, atualização, exclusão e listagem.
- Organização de tarefas com acompanhamento de status, responsáveis e prazos.
- Biblioteca de ativos digitais vinculados a cada projeto.
- Convite de membros e controle de papéis dentro do projeto.
- Feed consolidado de atividades recentes para manter a equipe sincronizada.
- API JSON com suporte a CORS, construída sobre `http.server` com roteamento e middleware próprios.
- Banco de dados SQLite com integridade referencial e migrações automáticas na inicialização.
- Testes de integração abrangentes usando somente a biblioteca padrão (`unittest` e `http.client`).

## Estrutura do projeto

```
backend/
  app.py           # Servidor HTTP, rotas e regras de negócio
  auth.py          # Hash de senhas e geração/validação de tokens JWT
  database.py      # Conexão e criação do esquema SQLite
  errors.py        # Exceções HTTP padronizadas
  router.py        # Roteador extremamente leve
  utils.py         # Objetos auxiliares de request/response
storage/
  studio.db        # Banco de dados SQLite (gerado em tempo de execução)
tests/
  test_app.py      # Testes de integração ponta a ponta
```

## Executando o servidor

1. Certifique-se de usar Python 3.11 ou superior.
2. (Opcional) Defina variáveis de ambiente para customizar a execução:
   - `DATABASE_PATH`: caminho completo para o arquivo SQLite (padrão: `storage/studio.db`).
   - `JWT_SECRET`: chave secreta utilizada para assinar tokens (padrão: `change-me`).
3. Inicialize o servidor:

```bash
python -m backend.app
```

Por padrão o serviço sobe em `http://0.0.0.0:8000`. Utilize ferramentas como `curl` ou qualquer cliente HTTP para consumir os endpoints. Exemplo de criação de projeto após autenticação:

```bash
# Registro de usuário
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "name": "Ana", "password": "senha-super-segura"}'

# Login para obter o token JWT
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "password": "senha-super-segura"}' | jq -r '.token')

# Criação de um projeto
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d '{"name": "Novo Álbum", "description": "Produção musical", "status": "active"}'
```

## Endpoints principais

| Método | Caminho | Descrição |
| ------ | ------- | --------- |
| `POST` | `/api/auth/register` | Cria um novo usuário e retorna token JWT. |
| `POST` | `/api/auth/login` | Autentica usuário existente. |
| `GET` | `/api/projects` | Lista projetos visíveis para o usuário logado. |
| `POST` | `/api/projects` | Cria um novo projeto. |
| `GET` | `/api/projects/:project_id` | Obtém detalhes completos do projeto (membros, tarefas, ativos). |
| `PUT` | `/api/projects/:project_id` | Atualiza dados do projeto (somente proprietário). |
| `DELETE` | `/api/projects/:project_id` | Remove projeto (somente proprietário). |
| `POST` | `/api/projects/:project_id/tasks` | Cria tarefa associada ao projeto. |
| `PATCH` | `/api/tasks/:task_id` | Atualiza atributos da tarefa. |
| `POST` | `/api/projects/:project_id/assets` | Anexa novo ativo ao projeto. |
| `GET` | `/api/projects/:project_id/assets` | Lista ativos do projeto. |
| `POST` | `/api/projects/:project_id/members` | Adiciona membro existente ao projeto (somente proprietário). |
| `GET` | `/api/activity` | Feed de atividades relevantes ao usuário. |
| `GET` | `/health` | Verificação simples de saúde. |

## Testes automatizados

Os testes de integração validam todo o fluxo de autenticação, criação de projeto, tarefas, ativos e feed de atividades.

```bash
python -m unittest
```

Durante a execução dos testes, um banco SQLite temporário é criado no diretório de arquivos temporários do sistema.

## Boas práticas adicionais

- Configure um valor forte para `JWT_SECRET` em produção.
- Faça backup do arquivo SQLite (`storage/studio.db`) ou substitua por um banco externo se necessário.
- O servidor suporta CORS básico (`Access-Control-Allow-Origin: *`), permitindo integração direta com front-ends hospedados em domínios diferentes.
- Para automatizar a execução, utilize gerenciadores de processos como `systemd`, `supervisord` ou containers Docker.

Bom desenvolvimento! :rocket:

