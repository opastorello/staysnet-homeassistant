# Stays.net para Home Assistant

Integração (HACS) que traz as **reservas** e os **dados do hóspede** do Stays.net para o Home Assistant, para automatizar a hospedagem: ligar o ar-condicionado antes do check-in, gerar a senha da fechadura, desligar tudo após o check-out, avisar a limpeza etc.

> Usa a API **não oficial** do Stays.net (mesmo login do painel web). Se o Stays mudar o painel, a integração pode quebrar. O Stays exige troca periódica de senha: quando expirar, o HA abre um fluxo de reautenticação.

Requer Home Assistant **2026.10.0** ou superior.

## Instalação (HACS)

1. HACS → ⋮ → *Repositórios personalizados* → adicione a URL deste repositório, categoria **Integração**.
2. Instale **Stays.net** e reinicie o Home Assistant.
3. *Configurações → Dispositivos e serviços → Adicionar integração → Stays.net*.
4. Entre com e-mail/senha do Stays.net e **selecione as propriedades** que quer acompanhar.

Depois é possível mudar propriedades, horários de check-in/out, janela de dias e intervalo de atualização em *Configurar*.

## Entidades (por propriedade)

Cada propriedade selecionada vira um dispositivo com:

| Entidade | Descrição |
|---|---|
| `calendar.<propriedade>` | Todas as reservas (evento = nome do hóspede). Estado "on" durante a estadia. |
| `binary_sensor.<propriedade>_ocupado` | Ligado entre check-in e check-out. |
| `sensor.<propriedade>_status_da_estadia` | `vago`, `check-in hoje`, `ocupado`, `check-out hoje`. |
| `sensor.<propriedade>_hospede` | Hóspede atual (ou o próximo). Atributos: `guest_name`, `guest_phone`, `guest_email`, `guest_count`, `channel`, `reservation_id`, `confirmation_code`, `door_code`, `check_in`, `check_out`, `which` (`current`/`next`). |
| `sensor.<propriedade>_proximo_check_in` | Timestamp do próximo check-in. |
| `sensor.<propriedade>_proximo_check_out` | Timestamp do check-out da estadia atual/próxima. |

- As reservas vêm só com **datas**; o horário usa o check-in/check-out padrão cadastrado em cada propriedade no Stays. As opções (padrão 14:00 / 11:00) só valem como fallback.
- `door_code` = últimos 4 dígitos do telefone do hóspede (convenção comum para fechaduras).
- CPF e endereço do hóspede **não** são expostos de propósito.
- Reservas canceladas, bloqueios e manutenção são ignorados.

## Exemplos de automação

Ligar o ar 2 h antes do check-in (usa o gatilho nativo de calendário):

```yaml
triggers:
  - trigger: calendar
    event: start
    offset: "-2:00:00"
    entity_id: calendar.casa_cynthia
actions:
  - action: climate.set_temperature
    target: { entity_id: climate.sala }
    data: { temperature: 23 }
```

Criar a senha da fechadura quando o hóspede chega:

```yaml
triggers:
  - trigger: calendar
    event: start
    entity_id: calendar.casa_cynthia
actions:
  - action: zha.set_lock_user_code   # ajuste para a sua fechadura
    target: { entity_id: lock.porta }
    data:
      code_slot: 5
      user_code: "{{ state_attr('sensor.casa_cynthia_hospede', 'door_code') }}"
```

Desligar tudo e avisar a limpeza no check-out:

```yaml
triggers:
  - trigger: calendar
    event: end
    entity_id: calendar.casa_cynthia
actions:
  - action: light.turn_off
    target: { area_id: casa_cynthia }
  - action: notify.mobile_app_limpeza
    data: { message: "Check-out feito em Casa Cynthia." }
```

## Desenvolvimento

Código em `custom_components/staysnet/`. O cliente (`api.py`) é um port para `aiohttp` do `StaysClient` do `stays-sync` (login em 3 passos → cookie `hdbid_sn_<system>`, JSON-RPC `reserve.getCalendarEx` e `reserve.getReserveForCalendar`).
