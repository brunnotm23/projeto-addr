# Cenário 4: Simulação de Instabilidade de Rede (Jitter)

## 1. O que é Jitter?
O **Jitter** é a variação estatística do atraso (latência) na entrega de pacotes sucessivos dentro de uma rede. Em um cenário ideal, os pacotes chegariam com um intervalo constante; no mundo real, fatores como congestionamento dinâmico, interferências em meios compartilhados (como RF/Wi-Fi) e variações no processamento de roteadores causam essa oscilação imprevisível.

## 2. Objetivo do Cenário
Este cenário visa avaliar a resiliência do sistema IoT diante de uma rede instável. Diferente do Cenário 2 (queda total), aqui a rede permanece ativa, mas a **previsibilidade** do tempo de resposta é degradada. O objetivo é observar como a incerteza no tempo de chegada dos logs afeta a ocupação do buffer e a latência final.

## 3. Como a Simulação Ocorre
A lógica de transmissão foi modificada especificamente para este fluxo:
* **Cálculo de Atraso Composto:** O tempo de rede deixa de ser uma distribuição exponencial pura e passa a ser a soma de dois componentes:
    1.  **Atraso Base:** Segue a distribuição exponencial $M/M/1$ padrão da largura de banda.
    2.  **Componente Jitter:** Uma variável aleatória uniforme definida pelo parâmetro `JITTER_MAX`.
* **Fórmula Aplicada:** $$Atraso_{total} = Atraso_{base} + \text{Uniforme}(0, \text{JITTER\_MAX})$$
* **Impacto no Fluxo:** Cada log sofre um atraso variável antes de chegar à CPU, simulando a instabilidade típica de canais de rádio frequência.

## 4. Importância deste Cenário
* **Análise de Dispersão:** Permite observar o "alargamento" da base no histograma de latência, indicando perda de determinismo.
* **Dimensionamento de Buffer:** Ajuda a verificar se a capacidade da fila ($K=50$) é suficiente para absorver pequenos surtos de chegada causados pelo represamento momentâneo de pacotes.
* **Qualidade de Serviço (QoS):** É fundamental para validar se os requisitos de tempo do projeto IoT são atendidos mesmo sob condições de rede não ideais, o que é crítico para aplicações de monitoramento em tempo real.