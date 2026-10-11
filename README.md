<p align="center">
  <img src="custom_components/staysnet/brand/icon@2x.png" width="120" alt="Stays.net">
</p>

# Stays.net para Home Assistant

Traz as **reservas** e os **dados do hóspede** do Stays.net para o Home Assistant, para automatizar a hospedagem: preparar a casa antes da chegada, criar a senha da fechadura, desligar tudo no check-out, avisar a limpeza, economizar energia com a casa vaga.

[![Abrir no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=opastorello&repository=staysnet-homeassistant&category=integration)
[![Adicionar a integração](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=staysnet)

> Usa a API **não oficial** do Stays.net (o mesmo login do painel web). Se o Stays mudar o painel, a integração pode quebrar. O Stays exige troca periódica de senha: quando expirar, o HA abre um fluxo de reautenticação.

Requer Home Assistant **2026.10.0** ou superior.

## Instalação em 3 cliques

1. Clique em **Abrir no HACS** acima → *Baixar* → reinicie o Home Assistant.
2. Clique em **Adicionar a integração** → entre com o e-mail e a senha do Stays.net → **selecione as propriedades**.
3. Importe os blueprints da seção [Automações prontas](#automações-prontas).

(Manual: HACS → ⋮ → *Repositórios personalizados* → `opastorello/staysnet-homeassistant`, categoria *Integração*.)

Em *Configurar* você muda as propriedades, a janela de dias e o intervalo de atualização. Os horários de check-in/out vêm do cadastro de cada propriedade no Stays; as opções de horário só valem como reserva (fallback).

## Entidades (por propriedade)

| Entidade | O que mostra |
|---|---|
| **Calendário** `calendar.<propriedade>` | Todas as reservas (evento = nome do hóspede). Ligado durante a estadia. |
| **Calendário** "Bloqueios" | Bloqueios e manutenção, como eventos de dia inteiro. |
| **Ocupado** | Ligado entre o check-in e o check-out. |
| **Check-in hoje** / **Check-out hoje** | Há chegada / saída hoje (mesmo que já tenha passado). |
| **Virada no mesmo dia** | Check-out e check-in no mesmo dia (prioridade para a limpeza). |
| **Status da estadia** | `vago`, `check-in hoje`, `ocupado`, `check-out hoje`. |
| **Hóspede** | Quem está na casa (ou o próximo). Atributos: nome, telefone, e-mail, nº de hóspedes, canal, `door_code`, datas… |
| **Próximo hóspede** / **Último hóspede** | Mesmos atributos para a próxima reserva e para a última encerrada. |
| **Reserva**, **Canal**, **Nº de hóspedes**, **Noites** | Dados da estadia atual (ou da próxima). |
| **Próximo check-in** / **Próximo check-out** / **Último check-out** | Timestamps, bons como gatilho de tempo. |
| **Dias para o check-in** / **Dias vago** | Contagem até a próxima chegada. |
| **Noites restantes** / **Noite atual** / **Progresso da estadia** | Linha do tempo da estadia em curso. |
| **Chegadas em 7 dias** / **Reservas futuras** | Contadores. |
| **Ocupação do mês** | % das noites do mês reservadas. |
| **Eventos da estadia** | Entidade de evento (veja abaixo). |

E, na conta: **Atualizar agora** (botão) e **Última atualização** (diagnóstico).

- As reservas vêm só com **datas**; o horário usa o check-in/check-out padrão da propriedade no Stays.
- `door_code` = últimos 4 dígitos do telefone do hóspede; sem telefone (comum no Booking.com), os últimos 4 dígitos do código de confirmação. O atributo `door_code_source` diz de onde veio (`phone` ou `confirmation_code`).
- CPF e endereço do hóspede **não** são expostos. E-mail, telefone, `door_code` e código de confirmação **não são gravados no histórico** (recorder).
- Reservas canceladas são ignoradas.

## Gatilhos

A entidade **Eventos da estadia** e eventos no barramento (`staysnet_<evento>`) disparam em:

| Evento | Quando |
|---|---|
| `check_in` | No horário de check-in (a estadia começa). |
| `check_out` | No horário de check-out (a estadia termina). |
| `reservation_created` | Entrou uma reserva nova. |
| `reservation_changed` | Mudaram as datas, o nº de hóspedes ou o nome. Traz `changes` com antes/depois. |
| `reservation_cancelled` | A reserva sumiu do Stays (só depois de **2 consultas seguidas**, para não dar alarme falso). |

Os dados do evento (`property`, `reservation_id`, `guest_name`, `guest_count`, `channel`, `check_in`, `check_out`) **não** incluem telefone, e-mail nem `door_code`, porque eventos vão para o histórico. Para esses dados use a ação abaixo.

Além disso, o gatilho de **calendário** do HA (início/fim, com *offset*) continua disponível, e os sensores de *Próximo check-in/out* servem como gatilho de tempo.

## Ação `staysnet.get_stay`

Devolve os dados da estadia **na hora, sem gravar nada no histórico**: nome, telefone, e-mail, nº de hóspedes, canal, `door_code`, datas, noites.

```yaml
- action: staysnet.get_stay
  data:
    entity_id: event.casa_mae_eventos_da_estadia   # qualquer entidade da propriedade
    which: auto        # auto | current | next | last
  response_variable: stay
- action: notify.mobile_app_meu_celular
  data:
    message: "{{ stay.guest_name }} chega com o código {{ stay.door_code }}"
```

Se não houver estadia, volta `found: false`.

## Automações prontas

Blueprints: você escolhe a propriedade e **só diz o que fazer**. Dentro das ações, a variável `stay` já traz os dados do hóspede.

| Blueprint | Faz o quê | Importar |
|---|---|---|
| **Chegada do hóspede** | Roda suas ações no check-in (luzes, clima, boas-vindas). | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Fguest_arrival.yaml) |
| **Saída do hóspede** | Roda suas ações no check-out (desligar tudo, travar, modo eco). | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Fguest_departure.yaml) |
| **Preparar a casa antes da chegada** | Roda suas ações X horas antes do check-in (ligar o ar). | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Fpre_arrival.yaml) |
| **Senha da fechadura** | Cria a senha antes do check-in e apaga no check-out. | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Fdoor_code.yaml) |
| **Aviso de reserva nova/alterada/cancelada** | Notifica você, com a mensagem pronta. | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Freservation_alert.yaml) |
| **Aviso para a limpeza** | No check-out avisa a limpeza e destaca **virada no mesmo dia**. | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Fcleaning_alert.yaml) |
| **Modo economia com a casa vaga** | Depois de X horas vaga e sem chegada próxima, roda suas ações de economia. | [![Importar](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fopastorello%2Fstaysnet-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fstaysnet%2Fvacant_savings.yaml) |

Depois de importar: *Configurações → Automações → Criar automação → (o blueprint)*.

### Exemplo sem blueprint

```yaml
triggers:
  - trigger: calendar
    event: start
    offset: "-2:00:00"
    entity_id: calendar.casa_mae
actions:
  - action: climate.set_temperature
    target: { entity_id: climate.sala }
    data: { temperature: 23 }
```

## Diagnóstico

*Dispositivos e serviços → Stays.net → ⋮ → Baixar diagnóstico*: sem credenciais nem dados de hóspedes. Mostra as datas exatamente como o Stays enviou, os horários por propriedade e quais campos de horário a reserva traz.

## Desenvolvimento

Código em `custom_components/staysnet/`. O cliente (`api.py`) é um port para `aiohttp` do `StaysClient` do `stays-sync` (login em 3 passos → cookie `hdbid_sn_<system>`, JSON-RPC `reserve.getCalendarEx` e `reserve.getReserveForCalendar`).

Testes (rodam contra o Home Assistant real; no Windows use Docker, pois o harness precisa de `fcntl`):

```bash
pip install -r requirements_test.txt
pytest
```
