import simpy
import random
import numpy as np
import matplotlib.pyplot as plt

# =======================================================================
# --- 1. PARÂMETROS GERAIS DO SISTEMA DISTRIBUÍDO E REDE ---
# =======================================================================

# Tráfego
LAMBDA_IOT = 100.0         # Taxa de chegada de logs dos sensores (logs/s)
LAMBDA_CONSULTA = 10.0     # Taxa de buscas feitas por usuários externos (consultas/s)

# Rede (Camada Física e Enlace)
LARGURA_BANDA = 1e6        # 1 Mbps
TAMANHO_LOG_AVG = 1024 * 8 # 1 KB de payload médio
DISTANCIA = 500            # Metros
VEL_SINAL = 2e8            # Velocidade de propagação
BER_BASE = 1e-6            # Erro de bit
CAPACIDADE_BUFFER = 50     # K da Fila do Gateway
CABECALHO = 32 * 8         # Overhead de Enquadramento
LIMITE_TENTATIVAS = 3      # ARQ (Stop-and-Wait)

# Servidores (Data Center / Nuvem)
CAPACIDADE_CPU = 1         # Quantidade de processadores
CAPACIDADE_DISCO = 1       # Quantidade de discos de armazenamento
TEMPO_PROC_AVG = 0.005     # Tempo médio de processamento (5ms)
TEMPO_DISCO_AVG = 0.005    # Tempo médio de gravação no disco (5ms)
TEMPO_BUSCA_AVG = 0.010    # Tempo médio de leitura/busca no disco (10ms)

# =======================================================================
# --- 2. COLETA DE DADOS (MÉTRICAS) ---
# =======================================================================
stats = {
    'logs_gerados': 0,
    'logs_perda_buffer': 0,
    'logs_erro_bit': 0,
    'total_retransmissoes': 0,
    'logs_armazenados': 0,
    'consultas_geradas': 0,
    'consultas_completas': 0,
    'latencia_rede': [],          # Tempo só na camada de comunicação
    'latencia_ponta_a_ponta': [], # Tempo desde a criação até salvar no disco
    'latencia_recuperacao': []    # Tempo que o usuário espera pela busca
}

# =======================================================================
# --- 3. PROCESSOS DO SISTEMA ---
# =======================================================================

def fluxo_completo_log(env, nome, canal_rf, cpu, disco, stats):
    """Simula o ciclo de vida ponta a ponta de um log."""
    chegada_sistema = env.now
    stats['logs_gerados'] += 1
    
    # ---------------------------------------------------------
    # FASE 1: COMUNICAÇÃO DE DADOS (Enlace e Físico)
    # ---------------------------------------------------------
    if len(canal_rf.queue) >= CAPACIDADE_BUFFER:
        stats['logs_perda_buffer'] += 1
        return # Pacote descartado na rede

    payload_bits = random.expovariate(1.0 / TAMANHO_LOG_AVG)
    tamanho_total_bits = payload_bits + CABECALHO

    sucesso_rede = False
    tentativas = 0

    while tentativas < LIMITE_TENTATIVAS and not sucesso_rede:
        tentativas += 1
        with canal_rf.request() as req_rede:
            yield req_rede # Aguarda o meio ficar livre
            
            ber_atual = BER_BASE * random.uniform(0.5, 1.5)
            atraso_tx = tamanho_total_bits / LARGURA_BANDA
            atraso_prop = DISTANCIA / VEL_SINAL
            
            yield env.timeout(atraso_tx + atraso_prop)
            
            prob_sucesso = (1 - ber_atual) ** tamanho_total_bits
            if random.random() < prob_sucesso:
                sucesso_rede = True
                stats['latencia_rede'].append(env.now - chegada_sistema)
            else:
                if tentativas < LIMITE_TENTATIVAS:
                    stats['total_retransmissoes'] += 1
                else:
                    stats['logs_erro_bit'] += 1

    if not sucesso_rede:
        return # O dado "morreu" na rede e não chega aos servidores

    # ---------------------------------------------------------
    # FASE 2: SISTEMA DISTRIBUÍDO (Processamento e Armazenamento)
    # ---------------------------------------------------------
    # Passa pela CPU para validação/parsing
    with cpu.request() as req_cpu:
        yield req_cpu
        yield env.timeout(random.expovariate(1.0 / TEMPO_PROC_AVG))
        
    # Salva no Banco de Dados / Disco
    with disco.request() as req_disco:
        yield req_disco
        yield env.timeout(random.expovariate(1.0 / TEMPO_DISCO_AVG))
        
    stats['logs_armazenados'] += 1
    stats['latencia_ponta_a_ponta'].append(env.now - chegada_sistema)


def fluxo_recuperacao_usuario(env, cpu, disco, stats):
    """Gera requisições de usuários externos querendo ler logs."""
    while True:
        yield env.timeout(random.expovariate(LAMBDA_CONSULTA))
        stats['consultas_geradas'] += 1
        env.process(executar_busca(env, cpu, disco, stats))

def executar_busca(env, cpu, disco, stats):
    """Simula a carga de uma query de recuperação no sistema."""
    inicio_busca = env.now
    
    with cpu.request() as req_cpu:
        yield req_cpu
        yield env.timeout(0.002) # Overhead leve de processar a query
        
        with disco.request() as req_disco:
            yield req_disco
            # O disco é o gargalo: demora mais para buscar do que para escrever
            yield env.timeout(random.expovariate(1.0 / TEMPO_BUSCA_AVG))
            
    stats['consultas_completas'] += 1
    stats['latencia_recuperacao'].append(env.now - inicio_busca)


def gerador_trafego_iot(env, canal_rf, cpu, disco, stats):
    """Gera a chegada de logs dos dispositivos."""
    i = 0
    while True:
        yield env.timeout(random.expovariate(LAMBDA_IOT))
        i += 1
        env.process(fluxo_completo_log(env, f'Log_{i}', canal_rf, cpu, disco, stats))


# =======================================================================
# --- 4. EXECUÇÃO DA SIMULAÇÃO ---
# =======================================================================
print("--- Iniciando Simulação End-to-End do Ecossistema IoT ---")
env = simpy.Environment()

# Inicialização dos Recursos Compartilhados
canal_rf = simpy.Resource(env, capacity=1)
servidor_cpu = simpy.Resource(env, capacity=CAPACIDADE_CPU)
armazenamento_disco = simpy.Resource(env, capacity=CAPACIDADE_DISCO)

# Injeção de Processos no Ambiente
env.process(gerador_trafego_iot(env, canal_rf, servidor_cpu, armazenamento_disco, stats))
env.process(fluxo_recuperacao_usuario(env, servidor_cpu, armazenamento_disco, stats))

# Roda o mundo por 1000 segundos
env.run(until=1000) 


# =======================================================================
# --- 5. RELATÓRIO CIENTÍFICO ---
# =======================================================================
print("\n[MÉTRICAS DA CAMADA DE REDE (IoT -> Gateway)]")
print(f"Total de Logs Gerados pelo IoT: {stats['logs_gerados']}")
print(f"Descartes por Buffer Cheio: {stats['logs_perda_buffer']}")
print(f"Falhas Irrecuperáveis (Erro de Bit): {stats['logs_erro_bit']}")
print(f"Total de Retransmissões (ARQ): {stats['total_retransmissoes']}")
print(f"Latência Média de Rede: {np.mean(stats['latencia_rede'])*1000:.2f} ms")

print("\n[MÉTRICAS DO SISTEMA DISTRIBUÍDO (Backend)]")
print(f"Logs Salvos com Sucesso no Disco: {stats['logs_armazenados']}")
print(f"Consultas Completadas p/ Usuários: {stats['consultas_completas']}")
print(f"Latência Média PONTA-A-PONTA (IoT -> Disco): {np.mean(stats['latencia_ponta_a_ponta'])*1000:.2f} ms")
print(f"Tempo Médio de Recuperação p/ Usuário: {np.mean(stats['latencia_recuperacao'])*1000:.2f} ms")

# =======================================================================
# --- 6. GERAÇÃO DE GRÁFICOS PARA O ARTIGO ---
# =======================================================================
plt.figure(figsize=(12, 5))

# Gráfico 1: Atraso End-to-End do Log
plt.subplot(1, 2, 1)
plt.hist([t * 1000 for t in stats['latencia_ponta_a_ponta']], bins=30, color='skyblue', edgecolor='black')
plt.title('Latência Ponta-a-Ponta (Ingestão)')
plt.xlabel('Tempo (ms)')
plt.ylabel('Frequência de Logs')

# Gráfico 2: Tempo de Espera do Usuário Externo
plt.subplot(1, 2, 2)
plt.hist([t * 1000 for t in stats['latencia_recuperacao']], bins=30, color='lightgreen', edgecolor='black')
plt.title('Tempo de Recuperação de Logs (Consultas)')
plt.xlabel('Tempo (ms)')
plt.ylabel('Frequência de Buscas')

plt.tight_layout()
plt.show()