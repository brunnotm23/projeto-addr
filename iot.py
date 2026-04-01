import simpy
import random
import numpy as np
import matplotlib.pyplot as plt

# --- 1. PARÂMETROS DO SISTEMA (DIMENSIONAMENTO) ---
LAMBDA = 100.0        # Taxa de chegada (logs/segundo) - Processo de Poisson
LARGURA_BANDA = 1e6  # 1 Mbps (Capacidade do canal em bits por segundo)
TAMANHO_LOG_AVG = 1024 * 8 # 1 KB em bits (Média do tamanho do pacote)

# --- 2. PARÂMETROS DE REDE (CAMADA FÍSICA/ENLACE) ---
DISTANCIA = 500      # 500 metros entre o dispositivo IoT e o Gateway
VEL_SINAL = 2e8      # Velocidade de propagação (~200.000 km/s)
BER_BASE = 1e-6           # Bit Error Rate Base (1 erro a cada 1 milhão de bits)
CAPACIDADE_BUFFER = 50 # Tamanho máximo da fila (K) para evitar estouro
CABECALHO = 32 * 8 # Cabeçalho de 32 bytes adicionado à cada log
LIMITE_TENTATIVAS = 3 # Máximo de tentativas de retransmissão no caso de falha (1 tentativa original + retransmissões)

# --- 3. COLETA DE DADOS ---
stats = {
    'tempos_residencia': [],
    'logs_sucesso': 0,
    'logs_erro_bit': 0,
    'logs_perda_buffer': 0,
    'total_retransmissoes': 0
}

def dispositivo_iot(env, nome, canal, stats):
    """Simula o ciclo de vida de um log na camada de enlace."""
    chegada = env.now
    
    # Verifica se há espaço no buffer (Dimensionamento de Sistema Distribuído)
    if len(canal.queue) >= CAPACIDADE_BUFFER:
        stats['logs_perda_buffer'] += 1
        return # Descarta o log (Packet Loss)
    
    # Tamanho do payload
    payload_bits = random.expovariate(1.0 / TAMANHO_LOG_AVG)
    tamanho_total_bits = payload_bits + CABECALHO

    # Registro de tentativas de transmissão
    sucesso = False
    tentativas_atuais = 0

    while tentativas_atuais < LIMITE_TENTATIVAS and not sucesso:
        tentativas_atuais += 1

        with canal.request() as request:
            yield request # Aguarda sua vez de transmitir (Acesso ao Meio)
            
            # --- CAMADA FÍSICA ---
            # BER Dinâmico: Simula pequena variação de ruído no canal
            ber_atual = BER_BASE * random.uniform(0.5, 1.5)
            
            atraso_transmissao = tamanho_total_bits / LARGURA_BANDA
            atraso_propagacao = DISTANCIA / VEL_SINAL
            
            # Ocupação do canal
            yield env.timeout(atraso_transmissao + atraso_propagacao)
            
            # Verificação de erro de bit no quadro
            prob_sucesso = (1 - ber_atual) ** tamanho_total_bits
            
            if random.random() < prob_sucesso:
                sucesso = True
                stats['logs_sucesso'] += 1
                stats['tempos_residencia'].append(env.now - chegada)
            else:
                # Se falhou, contabilizamos a tentativa frustrada
                if tentativas_atuais < LIMITE_TENTATIVAS:
                    stats['total_retransmissoes'] += 1
                    # O log volta para o loop para tentar transmitir de novo
                else:
                    stats['logs_erro_bit'] += 1

def gerador_trafego(env, canal, stats):
    """Injeta logs no sistema seguindo a taxa LAMBDA."""
    i = 0
    while True:
        yield env.timeout(random.expovariate(LAMBDA))
        i += 1
        env.process(dispositivo_iot(env, f'Log_{i}', canal, stats))

# --- 4. EXECUÇÃO DA SIMULAÇÃO ---
print("--- Iniciando Simulação de Desempenho IoT ---")
env = simpy.Environment()
canal = simpy.Resource(env, capacity=1) # Canal único (M/M/1/K)
env.process(gerador_trafego(env, canal, stats))
env.run(until=1000) # Simula por 1000 segundos

# --- 5. RELATÓRIO TÉCNICO ---
total_gerado = stats['logs_sucesso'] + stats['logs_erro_bit'] + stats['logs_perda_buffer']
vazao_util = (stats['logs_sucesso'] * TAMANHO_LOG_AVG) / 1000 # bits/seg

print(f"\nRESULTADOS DO DIMENSIONAMENTO:")
print(f"Total de Logs Gerados: {total_gerado}")
print(f"Logs Processados com Sucesso: {stats['logs_sucesso']}")
print(f"Logs Perdidos (Buffer Cheio): {stats['logs_perda_buffer']}")
print(f"Logs Corrompidos (Erro de Bit): {stats['logs_erro_bit']}")
print(f"Total de Retransmissões Realizadas: {stats['total_retransmissoes']}")
print("-" * 30)
print(f"Tempo Médio de Residência (W): {np.mean(stats['tempos_residencia']):.4f} s")
print(f"Vazão Útil (Goodput): {vazao_util / 1000:.2f} kbps")
print(f"Taxa de Perda Total: {((total_gerado - stats['logs_sucesso']) / total_gerado) * 100:.2f}%")

# Gráfico simples para o relatório
plt.hist(stats['tempos_residencia'], bins=30, edgecolor='black')
plt.title('Distribuição do Tempo de Residência dos Logs')
plt.xlabel('Tempo (s)')
plt.ylabel('Frequência')
plt.show()