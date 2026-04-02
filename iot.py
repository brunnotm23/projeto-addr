import simpy
import random
import numpy as np
import matplotlib.pyplot as plt

# =======================================================================
# --- 1. PARÂMETROS GERAIS DO SISTEMA DISTRIBUÍDO E REDE ---
# =======================================================================

# Tráfego
LAMBDA_IOT = 60.0         # Taxa de chegada de logs dos sensores (logs/s)
LAMBDA_CONSULTA = 10.0     # Taxa de buscas feitas por usuários externos (consultas/s)
TEMPO_SIMULACAO = 1000     # Tempo total de simulação
INTERVALO_MONITOR = 0.1    # Frequência de amostragem para métricas temporais

# Rede (Camada Física e Enlace)
LARGURA_BANDA = 1e6        # Larguda da Banda em bps
TAMANHO_LOG_AVG = 1024 * 8 # Payload médio em bits
CAPACIDADE_BUFFER = 50     # K da Fila do Gateway

# Taxa de serviço (mu) = Banda / Tamanho Médio
MU_REDE = LARGURA_BANDA / TAMANHO_LOG_AVG

# Servidores (Data Center / Nuvem)
CAPACIDADE_CPU = 1         # Quantidade de processadores
CAPACIDADE_DISCO = 1       # Quantidade de discos de armazenamento
TEMPO_PROC_AVG = 0.005     # Tempo médio de processamento 
TEMPO_PROC_QUERY_AVG = 0.002 # Tempo médio de processamento de query 
TEMPO_DISCO_AVG = 0.005    # Tempo médio de gravação no disco 
TEMPO_BUSCA_AVG = 0.010    # Tempo médio de leitura/busca no disco 

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
    'latencia_recuperacao': [],   # Tempo que o usuário espera pela busca
    'ocupacao_sistema': [],       # Amostras de (fila + servidor) ao longo do tempo
    'utilizacao_canal': [],       # 1 se ocupado, 0 se livre
    'amostras_tempo': []
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
    # M/M/1/K: Sistema bloqueia se fila + servidor estiverem cheios
    if (len(canal_rf.queue) + canal_rf.count) >= CAPACIDADE_BUFFER: 
        stats['logs_perda_buffer'] += 1
        return # Pacote descartado na rede

    with canal_rf.request() as req_rede:
        yield req_rede
        # M/M/1/K: Tempo de serviço deve ser exponencial
        yield env.timeout(random.expovariate(MU_REDE))
        stats['latencia_rede'].append(env.now - chegada_sistema)

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
        # Para M/M/1/K, todo tempo de servico deve ser exponencial
        yield env.timeout(random.expovariate(1.0 / TEMPO_PROC_QUERY_AVG)) 
        
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

def monitorar_sistema(env, canal_rf, stats):
    """Coleta amostras periódicas do estado da fila e do servidor."""
    while True:
        # N = itens na fila + item sendo servido
        n_sistema = len(canal_rf.queue) + canal_rf.count
        stats['ocupacao_sistema'].append(n_sistema)
        stats['utilizacao_canal'].append(canal_rf.count) # 1 ou 0
        stats['amostras_tempo'].append(env.now)
        yield env.timeout(INTERVALO_MONITOR)


# =======================================================================
# --- 4. EXECUÇÃO DA SIMULAÇÃO ---
# =======================================================================
print("--- Iniciando Simulacao End-to-End do Ecossistema IoT ---")
env = simpy.Environment()

# Inicialização dos Recursos Compartilhados (FCFS por padrão)
canal_rf = simpy.Resource(env, capacity=1)
servidor_cpu = simpy.Resource(env, capacity=CAPACIDADE_CPU)
armazenamento_disco = simpy.Resource(env, capacity=CAPACIDADE_DISCO)

# Injeção de Processos no Ambiente
env.process(gerador_trafego_iot(env, canal_rf, servidor_cpu, armazenamento_disco, stats))
env.process(fluxo_recuperacao_usuario(env, servidor_cpu, armazenamento_disco, stats))
env.process(monitorar_sistema(env, canal_rf, stats))

# Roda o mundo por 1000 segundos
env.run(until=TEMPO_SIMULACAO) 


# =======================================================================
# --- 5. RELATÓRIO CIENTÍFICO ---
# =======================================================================
prob_bloqueio = stats['logs_perda_buffer'] / stats['logs_gerados'] if stats['logs_gerados'] > 0 else 0
vazao_sucesso = stats['logs_armazenados'] / TEMPO_SIMULACAO
utilizacao_media = np.mean(stats['utilizacao_canal']) * 100
l_medio = np.mean(stats['ocupacao_sistema'])

# --- CÁLCULOS TEÓRICOS (M/M/1/K) PARA VALIDAÇÃO ---
mu = MU_REDE 
lambda_analise = LAMBDA_IOT 
rho = lambda_analise / mu
K = CAPACIDADE_BUFFER

if rho != 1.0:
    teorico_pb = (rho**K * (1 - rho)) / (1 - rho**(K + 1))
    teorico_L = (rho / (1 - rho)) - ((K + 1) * rho**(K + 1)) / (1 - rho**(K + 1))
else:
    teorico_pb = 1.0 / (K + 1)
    teorico_L = K / 2.0
teorico_W = teorico_L / (lambda_analise * (1 - teorico_pb))

print("\n[METRICAS DA CAMADA DE REDE (IoT -> Gateway)]")
print(f"Total de Logs Gerados pelo IoT: {stats['logs_gerados']}")
print(f"Descartes por Buffer Cheio: {stats['logs_perda_buffer']} (Prob. Bloqueio: {prob_bloqueio:.4f})")
print(f"Utilizacao Media do Canal (Rho): {utilizacao_media:.2f}%")
print(f"Latencia Media de Rede: {np.mean(stats['latencia_rede'])*1000:.2f} ms")
print(f"Numero Medio de Logs no Sistema (L): {l_medio:.2f}")

print("\n[VALIDACAO TEORICA M/M/1/K]")
print(f"Lambda (Taxa de Chegada): {LAMBDA_IOT:.2f} logs/s")
print(f"Mu (Taxa de Servico): {mu:.2f} logs/s")
print(f"Rho (Intensidade de Trafego): {rho:.4f}")
print(f"K (Capacidade do Sistema): {K}")
print("-" * 30)
print(f"Prob. Bloqueio: Simulado={prob_bloqueio:.4f} | Teorico={teorico_pb:.4f}")
print(f"Ocupacao Media (L): Simulado={l_medio:.2f} | Teorico={teorico_L:.2f}")
print(f"Latencia Media (W): Simulado={np.mean(stats['latencia_rede'])*1000:.2f} ms | Teorico={teorico_W*1000:.2f} ms")

print("\n[METRICAS DO SISTEMA DISTRIBUIDO (Backend)]")
print(f"Logs Salvos com Sucesso no Disco: {stats['logs_armazenados']}")
print(f"Consultas Completadas p/ Usuarios: {stats['consultas_completas']}")
print(f"Latencia Media PONTA-A-PONTA (IoT -> Disco): {np.mean(stats['latencia_ponta_a_ponta'])*1000:.2f} ms")
print(f"Tempo Medio de Recuperacao p/ Usuario: {np.mean(stats['latencia_recuperacao'])*1000:.2f} ms")

# =======================================================================
# --- 6. GERAÇÃO DE GRÁFICOS PARA O ARTIGO ---
# =======================================================================
plt.figure(figsize=(14, 10))

# Grafico 1: Histograma de Latencia Ponta-a-Ponta
plt.subplot(2, 2, 1)
plt.hist([t * 1000 for t in stats['latencia_ponta_a_ponta']], bins=30, color='skyblue', edgecolor='black')
plt.title('Distribuicao da Latencia Ponta-a-Ponta')
plt.xlabel('Tempo (ms)')
plt.ylabel('Frequencia')

# Grafico 2: Evolucao da Ocupacao do Buffer (K)
plt.subplot(2, 2, 2)
plt.plot(stats['amostras_tempo'][:1000], stats['ocupacao_sistema'][:1000], color='orange', linewidth=0.5)
plt.axhline(y=CAPACIDADE_BUFFER, color='r', linestyle='--', label='Capacidade K')
plt.title('Ocupacao do Sistema ao Longo do Tempo (Amostra Inicial)')
plt.xlabel('Tempo de Simulacao (s)')
plt.ylabel('Logs no Sistema (n)')
plt.legend()

# Grafico 3: Probabilidade dos Estados P(n)
plt.subplot(2, 2, 3)
n_counts = np.bincount(stats['ocupacao_sistema'], minlength=CAPACIDADE_BUFFER+1)
n_probs = n_counts / len(stats['ocupacao_sistema'])
plt.bar(range(len(n_probs)), n_probs, color='purple', alpha=0.7)
plt.title('Probabilidade de Estado P(n) - Simulado')
plt.xlabel('Numero de Logs no Sistema (n)')
plt.ylabel('Probabilidade')

# Grafico 4: CDF da Latencia de Rede
plt.subplot(2, 2, 4)
sorted_lat = np.sort(stats['latencia_rede'])
y_values = np.arange(len(sorted_lat)) / float(len(sorted_lat) - 1)
plt.plot(sorted_lat * 1000, y_values, marker='.', linestyle='none', color='green')
plt.title('CDF da Latencia de Rede')
plt.xlabel('Tempo (ms)')
plt.ylabel('Probabilidade Acumulada')

plt.tight_layout()
plt.show()