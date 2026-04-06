import simpy
import random
import numpy as np
import matplotlib.pyplot as plt

# =======================================================================
# --- 1. PARÂMETROS GERAIS E CONFIGURAÇÕES ---
# =======================================================================

# ESCALA BASE DO SISTEMA
N_DISPOSITIVOS = 10000

CONFIG = {
    'LAMBDA_IOT': N_DISPOSITIVOS * 0.01,          # Taxa de chegada agregada
    'LAMBDA_CONSULTA': 10,     # Consultas de usuários (consultas/s)
    'TEMPO_SIMULACAO': 3000,     # Tempo total (segundos)
    'INTERVALO_MONITOR': 0.1,    # Frequência do monitoramento
    'LARGURA_BANDA': 1e6,        # bps
    'TAMANHO_LOG_AVG': 1024 * 8, # bits
    'CAPACIDADE_BUFFER': 50,     # Capacidade da Fila (K)
    'CAPACIDADE_BACKLOG': N_DISPOSITIVOS * 60,   # Memória total do cluster (ex: 10 logs por dispositivo)
    'CAPACIDADE_CPU': 1,
    'CAPACIDADE_DISCO': 1,
    'TEMPO_PROC_AVG': 0.002,
    'TEMPO_PROC_QUERY_AVG': 0.002,
    'BACKOFF_BASE': 0.010,       # Tempo de espera base após falha de transmissão
    'TEMPO_DISCO_AVG': 0.003,
    'TEMPO_BUSCA_AVG': 0.010,
    'BER': 1e-6,                 # Bit Error Rate (Probabilidade de erro por bit)
    'TENTATIVAS': 2,            # Máximo de retransmissões antes de descartar
    'JITTER_FACTOR': 0.05,       # Flutuação de 15% na capacidade do canal
    'JITTER_MAX': 0.15,          # Jitter para o cenário 4
    'JANELA_RECONEXAO': 5.0,     # Janela maior para reduzir colisão na volta
    'BANDA_VARIAVEL_STEP': 50    # Frequência de alteração de banda para o cenário 3
}

CONFIG['MU_REDE'] = CONFIG['LARGURA_BANDA'] / CONFIG['TAMANHO_LOG_AVG']

# =======================================================================
# --- 2. PROCESSOS DO SISTEMA DISTRIBUÍDO ---
# =======================================================================

def fluxo_completo_log(env, nome, canal_rf, cpu, disco, stats):
    """Simula o ciclo de vida ponta a ponta de um log."""
    chegada_sistema = env.now
    stats['logs_gerados'] += 1
    
    # M/M/1/K: Bloqueia se a fila + servidor estiverem cheios
    if (len(canal_rf.queue) + canal_rf.count) >= CONFIG['CAPACIDADE_BUFFER']: 
        stats['logs_perda_buffer'] += 1
        return
    
    # --- Lógica de Transmissão com BER e Retentativas ---
    sucesso = False
    tentativas = 0
    
    while tentativas <= CONFIG['TENTATIVAS']:
        with canal_rf.request() as req_rede:
            yield req_rede
            
            # Aplica Jitter: O tempo de serviço base flutua conforme a qualidade do canal
            tempo_base = random.expovariate(CONFIG['MU_REDE'])
            jitter_mult = random.uniform(1 - CONFIG['JITTER_FACTOR'], 1 + CONFIG['JITTER_FACTOR'])
            yield env.timeout(tempo_base * jitter_mult)
            
            # PER (Packet Error Rate) = 1 - (1 - BER)^bits
            prob_erro_pacote = 1 - (1 - CONFIG['BER'])**CONFIG['TAMANHO_LOG_AVG']
            if random.random() > prob_erro_pacote:
                sucesso = True
                break
            else:
                tentativas += 1
                # Refinamento: Adiciona back-off exponencial para evitar re-tentativa imediata
                atraso_backoff = CONFIG['BACKOFF_BASE'] * (2 ** (tentativas - 1))
                yield env.timeout(atraso_backoff)
                stats['logs_retransmissoes'] += 1

    if sucesso:
        latencia_atual = env.now - chegada_sistema
        if stats['latencia_rede']:
            # Calcula o Jitter (Packet Delay Variation) comparando com o pacote anterior
            stats['jitter_rede'].append(abs(latencia_atual - stats['latencia_rede'][-1]))
        stats['latencia_rede'].append(latencia_atual)
    else:
        stats['logs_falha_transmissao'] += 1
        return

    with cpu.request() as req_cpu:
        yield req_cpu
        yield env.timeout(random.expovariate(1.0 / CONFIG['TEMPO_PROC_AVG']))
        
    with disco.request() as req_disco:
        yield req_disco
        yield env.timeout(random.expovariate(1.0 / CONFIG['TEMPO_DISCO_AVG']))
        
    stats['logs_armazenados'] += 1
    stats['tempos_fim_e2e'].append(env.now)

    stats['latencia_ponta_a_ponta'].append(env.now - chegada_sistema)


def fluxo_com_jitter(env, nome, canal_rf, cpu, disco, stats):
    """CENÁRIO 4: Simula o comportamento de Jitter com BER e Retransmissões."""
    chegada_sistema = env.now
    stats['logs_gerados'] += 1
    
    if (len(canal_rf.queue) + canal_rf.count) >= CONFIG['CAPACIDADE_BUFFER']: 
        stats['logs_perda_buffer'] += 1
        return

    sucesso = False
    tentativas = 0
    
    while tentativas <= CONFIG['TENTATIVAS']:
        with canal_rf.request() as req_rede:
            yield req_rede
            # Tempo de transmissão física (ocupa o canal)
            tempo_transmissao = random.expovariate(CONFIG['MU_REDE'])
            yield env.timeout(tempo_transmissao)
            
            # PER (Packet Error Rate)
            prob_erro_pacote = 1 - (1 - CONFIG['BER'])**CONFIG['TAMANHO_LOG_AVG']
            if random.random() > prob_erro_pacote:
                sucesso = True
                break
            else:
                tentativas += 1
                stats['logs_retransmissoes'] += 1

    if sucesso:
        # --- APLICAÇÃO DO JITTER DE REDE (Propagação) ---
        # Ocorre com o canal RF já liberado. Usamos Gaussiana para variância.
        atraso_jitter = max(0, random.gauss(0, CONFIG['JITTER_MAX'] / 2))
        yield env.timeout(atraso_jitter)

        latencia_atual = env.now - chegada_sistema
        if stats['latencia_rede']:
            stats['jitter_rede'].append(abs(latencia_atual - stats['latencia_rede'][-1]))
        stats['latencia_rede'].append(latencia_atual)
    else:
        stats['logs_falha_transmissao'] += 1
        return

    # Processamento no Servidor
    with cpu.request() as req_cpu:
        yield req_cpu
        yield env.timeout(random.expovariate(1.0 / CONFIG['TEMPO_PROC_AVG']))
        
    with disco.request() as req_disco:
        yield req_disco
        yield env.timeout(random.expovariate(1.0 / CONFIG['TEMPO_DISCO_AVG']))
        
    stats['logs_armazenados'] += 1
    stats['tempos_fim_e2e'].append(env.now)

    stats['latencia_ponta_a_ponta'].append(env.now - chegada_sistema)

def fluxo_recuperacao_usuario(env, cpu, disco, stats):
    """Gera requisições de usuários externos."""
    while True:
        yield env.timeout(random.expovariate(CONFIG['LAMBDA_CONSULTA']))
        stats['consultas_geradas'] += 1
        env.process(executar_busca(env, cpu, disco, stats))

def executar_busca(env, cpu, disco, stats):
    """Simula a carga de uma query."""
    inicio_busca = env.now
    
    with cpu.request() as req_cpu:
        yield req_cpu
        yield env.timeout(random.expovariate(1.0 / CONFIG['TEMPO_PROC_QUERY_AVG'])) 
        
        with disco.request() as req_disco:
            yield req_disco
            yield env.timeout(random.expovariate(1.0 / CONFIG['TEMPO_BUSCA_AVG']))
            
    stats['consultas_completas'] += 1
    stats['latencia_recuperacao'].append(env.now - inicio_busca)

def gerador_trafego_iot(env, canal_rf, cpu, disco, stats, estado_rede, cenario = ''):
    """Gera a chegada de logs. Se a rede cair, acumula no backlog."""
    i = 0
    while True:
        i += 1
        
        if estado_rede['sinal_ativo']:
            if cenario == '4':
                env.process(fluxo_com_jitter(env, f'Log_{i}', canal_rf, cpu, disco, stats))
            else:
                env.process(fluxo_completo_log(env, f'Log_{i}', canal_rf, cpu, disco, stats))
        else:
            # Verifica se o dispositivo ainda tem memória para armazenar o log
            if estado_rede['backlog'] < CONFIG['CAPACIDADE_BACKLOG']:
                estado_rede['backlog'] += 1
            else:
                stats['logs_perda_memoria_dispositivo'] += 1

def disparar_log_com_atraso(env, atraso, nome, canal_rf, cpu, disco, stats):
    """Função auxiliar: Espera um tempinho aleatório antes de tentar enviar o log."""
    yield env.timeout(atraso)
    # Chama o fluxo principal (que em breve terá a lógica de retransmissão do seu colega)
    env.process(fluxo_completo_log(env, nome, canal_rf, cpu, disco, stats))

def evento_queda_sinal(env, canal_rf, cpu, disco, stats, estado_rede):
    """Cenário 2: Simula uma queda de rede e a reconexão com Jitter (espalhamento)."""
    yield env.timeout(CONFIG['TEMPO_SIMULACAO']/3)
    print(f"\n[{env.now:.1f}s] ALERTA: Sinal de RF caiu! Dispositivos acumulando logs...")
    estado_rede['sinal_ativo'] = False
    
    yield env.timeout(CONFIG['TEMPO_SIMULACAO']/4)
    print(f"[{env.now:.1f}s] ALERTA: Sinal restaurado! Enviando {estado_rede['backlog']} logs retidos...")
    estado_rede['sinal_ativo'] = True
    
    # Aplica o Staggering Jitter: Evita o "thundering herd" no canal RF
    for i in range(estado_rede['backlog']):
        atraso_aleatorio = random.uniform(0.0, CONFIG['JANELA_RECONEXAO'])
        env.process(disparar_log_com_atraso(
            env, atraso_aleatorio, f'Log_Backlog_{i}', canal_rf, cpu, disco, stats
        ))
    
    estado_rede['backlog'] = 0

def evento_banda_dinamica(env):
    """CENÁRIO 3: Altera a largura de banda ciclicamente para simular condições variáveis de rede."""
    niveis_banda = [200e3, 500e3, 2e6, 1e6] # Oscila entre péssima e excelente
    idx = 0
    while True:
        CONFIG['LARGURA_BANDA'] = niveis_banda[idx]
        CONFIG['MU_REDE'] = CONFIG['LARGURA_BANDA'] / CONFIG['TAMANHO_LOG_AVG']
        print(f"[{env.now:.1f}s] BANDA DINÂMICA: Canal alterado para {CONFIG['LARGURA_BANDA']/1e3:.0f} kbps")
        
        yield env.timeout(CONFIG['BANDA_VARIAVEL_STEP'])
        idx = (idx + 1) % len(niveis_banda)

def monitorar_sistema(env, canal_rf, stats):
    """Coleta amostras do estado da fila."""
    while True:
        n_sistema = len(canal_rf.queue) + canal_rf.count
        stats['ocupacao_sistema'].append(n_sistema)
        stats['utilizacao_canal'].append(canal_rf.count)
        stats['amostras_tempo'].append(env.now)
        yield env.timeout(CONFIG['INTERVALO_MONITOR'])


# =======================================================================
# --- 3. FUNÇÕES DE RELATÓRIO E GRÁFICOS ---
# =======================================================================

def imprimir_relatorio(stats):
    """Calcula e imprime as métricas finais."""
    prob_bloqueio = stats['logs_perda_buffer'] / stats['logs_gerados'] if stats['logs_gerados'] > 0 else 0
    perda_dispositivo = stats['logs_perda_memoria_dispositivo'] / stats['logs_gerados'] if stats['logs_gerados'] > 0 else 0
    utilizacao_media = np.mean(stats['utilizacao_canal']) * 100
    l_medio = np.mean(stats['ocupacao_sistema'])
    taxa_retransmissao = stats['logs_retransmissoes'] / stats['logs_gerados'] if stats['logs_gerados'] > 0 else 0
    jitter_medio = np.mean(stats['jitter_rede']) * 1000 if stats['jitter_rede'] else 0

    print("\n" + "="*50)
    print(" RELATÓRIO FINAL DA SIMULAÇÃO ")
    print("="*50)
    print(f"Total de Logs Tentaram Entrar: {stats['logs_gerados']}")
    print(f"Descartes por Memória do Dispositivo (Outage): {stats['logs_perda_memoria_dispositivo']} ({perda_dispositivo:.2%})")
    print(f"Descartes por Buffer Cheio: {stats['logs_perda_buffer']} (Prob. Bloqueio: {prob_bloqueio:.4f})")
    print(f"Descartes por Erro de Transmissão (BER): {stats['logs_falha_transmissao']}")
    print(f"Total de Retransmissões Realizadas: {stats['logs_retransmissoes']} (Média: {taxa_retransmissao:.2f}/log)")
    print(f"Utilização Média do Canal: {utilizacao_media:.2f}%")
    
    latencia_rede = np.mean(stats['latencia_rede']) * 1000 if stats['latencia_rede'] else 0
    print(f"Latência Média de Rede (Age of Information): {latencia_rede:.2f} ms")
    print(f"Jitter Médio de Rede (PDV): {jitter_medio:.2f} ms")
    print(f"Número Médio de Logs no Sistema (L): {l_medio:.2f}")

    print(f"\n[BACKEND]")
    print(f"Logs Salvos com Sucesso: {stats['logs_armazenados']}")
    
    latencia_end = np.mean(stats['latencia_ponta_a_ponta']) * 1000 if stats['latencia_ponta_a_ponta'] else 0
    print(f"Latência Média E2E (Geração até Armazenamento): {latencia_end:.2f} ms")
    desvio = np.std(stats['latencia_ponta_a_ponta']) * 1000 if stats['latencia_ponta_a_ponta'] else 0
    print(f"Desvio Latência E2E: {desvio:.2f} ms")


def plotar_graficos(stats, cenario):
    """Plota os resultados visuais da simulação."""
    titulos = {
        '1': 'Cenário 1: Operação Normal (Tráfego Contínuo)',
        '2': 'Cenário 2: Falha de Sinal e Reconexão em Massa',
        '3': 'Cenário 3: Banda Instável',
        '4': 'Cenário 4: Instabilidade de Rede (Jitter)',
        '5': 'Comparativo Bandas (Baixa, Média e Alta)'
    }

    titulo_base = titulos.get(cenario, "Simulação IoT")

    plt.figure(figsize=(14, 15))

    # Gráfico 1: Ocupação do Buffer (Crucial para ver a reconexão em massa)
    plt.subplot(3, 1, 1)
    plt.plot(stats['amostras_tempo'], stats['ocupacao_sistema'], color='orange', linewidth=1)
    plt.axhline(y=CONFIG['CAPACIDADE_BUFFER'], color='r', linestyle='--', label='Capacidade Máxima (K)')
    plt.title(f'{titulo_base} - Ocupação do Gateway')
    plt.xlabel('Tempo de Simulação (s)')
    plt.ylabel('Logs no Sistema')
    plt.legend()

    # Gráfico 2: Latência vs Tempo de Simulação (Evolução Temporal)
    plt.subplot(3, 1, 2)
    if stats['latencia_ponta_a_ponta'] and stats['tempos_fim_e2e']:
        latencias_ms = [t * 1000 for t in stats['latencia_ponta_a_ponta']]
        plt.scatter(stats['tempos_fim_e2e'], latencias_ms, s=10, alpha=0.4, color='green', label='Log Individual')
        plt.title(f'{titulo_base} - Evolução da Latência E2E')
        plt.xlabel('Tempo de Simulação (s)')
        plt.ylabel('Latência (ms)')

    # Gráfico 3: Histograma de Latência
    plt.subplot(3, 1, 3)
    if stats['latencia_ponta_a_ponta']:
        plt.hist([t * 1000 for t in stats['latencia_ponta_a_ponta']], bins=50, color='skyblue', edgecolor='black')
        plt.title(f'{titulo_base} - Distribuição da Latência E2E (Histograma)')
        plt.xlabel('Tempo (ms)')
        plt.ylabel('Frequência')

    plt.tight_layout()
    
    # Adicione estes avisos aqui:
    print("\n[AVISO] Os gráficos foram abertos em uma nova janela.")
    print(">>> FECHE A JANELA DO GRÁFICO PARA LIBERAR O MENU E CONTINUAR <<<")

    plt.show()


# =======================================================================
# --- 4. MOTOR PRINCIPAL E MENU INTERATIVO ---
# =======================================================================

def executar_simulacao(cenario_escolhido):
    """Prepara o ambiente SimPy e roda baseado no cenário escolhido."""
    banda_kbps = CONFIG['LARGURA_BANDA'] / 1000
    print(f"\n--- Iniciando Simulação (Cenário {cenario_escolhido} | Banda: {banda_kbps} kbps) ---")
    
    # Estruturas de Dados zeradas para cada nova execução
    stats = {
        'logs_gerados': 0, 'logs_perda_buffer': 0, 'logs_armazenados': 0,
        'logs_perda_memoria_dispositivo': 0, 'logs_retransmissoes': 0, 
        'logs_falha_transmissao': 0,
        'consultas_geradas': 0, 'consultas_completas': 0,
        'latencia_rede': [], 'jitter_rede': [], 'latencia_ponta_a_ponta': [],
        'tempos_fim_e2e': [],
        'latencia_recuperacao': [],
        'ocupacao_sistema': [], 'utilizacao_canal': [], 'amostras_tempo': []
    }
    
    estado_rede = {'sinal_ativo': True, 'backlog': 0}
    
    # Inicializa o SimPy
    env = simpy.Environment()
    canal_rf = simpy.Resource(env, capacity=1)
    servidor_cpu = simpy.Resource(env, capacity=CONFIG['CAPACIDADE_CPU'])
    armazenamento_disco = simpy.Resource(env, capacity=CONFIG['CAPACIDADE_DISCO'])

    # Processos Padrões
    env.process(gerador_trafego_iot(env, canal_rf, servidor_cpu, armazenamento_disco, stats, estado_rede, cenario_escolhido))
    env.process(fluxo_recuperacao_usuario(env, servidor_cpu, armazenamento_disco, stats))
    env.process(monitorar_sistema(env, canal_rf, stats))

    # Processo Específico do Cenário 2
    if cenario_escolhido == '2':
        env.process(evento_queda_sinal(env, canal_rf, servidor_cpu, armazenamento_disco, stats, estado_rede))

    if cenario_escolhido == '3':
        env.process(evento_banda_dinamica(env))

    # Roda a simulação
    env.run(until=CONFIG['TEMPO_SIMULACAO']) 
    
    # Exibe resultados
    imprimir_relatorio(stats)
    plotar_graficos(stats, cenario_escolhido)


def plotar_graficos_comparativos(resultados):
    """Plota os resultados comparativos das 3 bandas sobrepostas."""
    plt.figure(figsize=(14, 15))
    cores = ['red', 'blue', 'green']
    
    # Gráfico 1: Ocupação do Buffer
    plt.subplot(3, 1, 1)
    for (nome, stats), cor in zip(resultados.items(), cores):
        plt.plot(stats['amostras_tempo'], stats['ocupacao_sistema'], label=nome, color=cor, linewidth=1.5, alpha=0.8)
        
    plt.axhline(y=CONFIG['CAPACIDADE_BUFFER'], color='black', linestyle='--', label='Capacidade Máxima (K)')
    plt.title('Comparativo: Ocupação do Gateway vs Largura de Banda')
    plt.xlabel('Tempo de Simulação (s)')
    plt.ylabel('Logs no Sistema')
    plt.legend()

    # Gráfico 2: Evolução da Latência E2E (Scatter) - Sobreposto
    plt.subplot(3, 1, 2)
    for (nome, stats), cor in zip(resultados.items(), cores):
        if stats['latencia_ponta_a_ponta'] and stats['tempos_fim_e2e']:
            latencias_ms = [t * 1000 for t in stats['latencia_ponta_a_ponta']]
            plt.scatter(stats['tempos_fim_e2e'], latencias_ms, s=10, alpha=0.3, label=nome, color=cor)
    plt.title('Comparativo: Evolução da Latência E2E ao longo do Tempo')
    plt.xlabel('Tempo de Simulação (s)')
    plt.ylabel('Latência (ms)')
    plt.legend()

    # Gráfico 3: Histograma de Latência - Sobreposto
    plt.subplot(3, 1, 3)
    for (nome, stats), cor in zip(resultados.items(), cores):
        if stats['latencia_ponta_a_ponta']:
            plt.hist([t * 1000 for t in stats['latencia_ponta_a_ponta']], bins=30, alpha=0.5, label=nome, color=cor, edgecolor='black')
            plt.xscale('log')
        
    plt.title('Comparativo: Distribuição da Latência Ponta-a-Ponta')
    plt.xlabel('Tempo (ms)')
    plt.ylabel('Frequência')
    plt.legend()

    plt.tight_layout()
    print("\n[AVISO] Os gráficos comparativos foram abertos em uma nova janela.")
    print(">>> FECHE A JANELA DO GRÁFICO PARA LIBERAR O MENU E CONTINUAR <<<")
    plt.show()

def executar_simulacao_comparativa():
    """Roda a simulação 3 vezes sequencialmente e compila os resultados visuais."""
    print("\n" + "="*45)
    print(" INICIANDO RODADA COMPARATIVA AUTOMÁTICA ")
    print("="*45)
    
    bandas = {
        'Baixa (100 Kbps)': 100e3,
        'Média (1 Mbps)': 1e6,
        'Alta (10 Mbps)': 10e6
    }
    resultados_stats = {}
    
    for nome, banda in bandas.items():
        print(f"\n[Simulando Perfil: {nome}]")
        CONFIG['LARGURA_BANDA'] = banda
        CONFIG['MU_REDE'] = CONFIG['LARGURA_BANDA'] / CONFIG['TAMANHO_LOG_AVG']
        
        # Estruturas zeradas para esta rodada
        stats = {
            'logs_gerados': 0, 'logs_perda_buffer': 0, 'logs_armazenados': 0,
            'logs_perda_memoria_dispositivo': 0, 'logs_retransmissoes': 0, 
            'logs_falha_transmissao': 0,
            'consultas_geradas': 0, 'consultas_completas': 0,
            'latencia_rede': [], 'jitter_rede': [], 'latencia_ponta_a_ponta': [],
            'tempos_fim_e2e': [],
            'latencia_recuperacao': [],
            'ocupacao_sistema': [], 'utilizacao_canal': [], 'amostras_tempo': []
        }
        estado_rede = {'sinal_ativo': True, 'backlog': 0}
        
        env = simpy.Environment()
        canal_rf = simpy.Resource(env, capacity=1)
        servidor_cpu = simpy.Resource(env, capacity=CONFIG['CAPACIDADE_CPU'])
        armazenamento_disco = simpy.Resource(env, capacity=CONFIG['CAPACIDADE_DISCO'])

        env.process(gerador_trafego_iot(env, canal_rf, servidor_cpu, armazenamento_disco, stats, estado_rede))
        env.process(fluxo_recuperacao_usuario(env, servidor_cpu, armazenamento_disco, stats))
        env.process(monitorar_sistema(env, canal_rf, stats))
        
        env.run(until=CONFIG['TEMPO_SIMULACAO']) 
        
        resultados_stats[nome] = stats
        
        # Micro relatório rápido
        latencia = np.mean(stats['latencia_ponta_a_ponta']) * 1000 if stats['latencia_ponta_a_ponta'] else 0
        prob_bloqueio = (stats['logs_perda_buffer'] / stats['logs_gerados']) * 100 if stats['logs_gerados'] > 0 else 0
        print(f" > Descartes por Buffer Cheio: {stats['logs_perda_buffer']} ({prob_bloqueio:.1f}%) | Latência Média: {latencia:.2f} ms")

    # Reseta pra não contaminar próximas execuções via menu
    CONFIG['LARGURA_BANDA'] = 1e6
    CONFIG['MU_REDE'] = CONFIG['LARGURA_BANDA'] / CONFIG['TAMANHO_LOG_AVG']
    
    plotar_graficos_comparativos(resultados_stats)

def main():
    """Menu principal do programa."""
    while True:
        print("\n" + "="*40)
        print(" SIMULADOR IoT E FILAS M/M/1/K ")
        print("="*40)
        print("Escolha o cenário que deseja executar:")
        print("1 - Cenário Normal (Tráfego Contínuo)")
        print("2 - Cenário de Falha (Queda de Sinal e Reconexão em Massa)")
        print("3 - Cenário com Banda Instável (Variável durante a execução)")
        print("4 - Cenário com Instabilidade de Rede (Jitter)")
        print("5 - Comparativo de Bandas (Baixa, Média e Alta)")
        print("0 - Sair do Programa")
        
        escolha = input("Digite a opção (0, 1, 2, 3, 4 ou 5): ").strip()
        
        if escolha == '0':
            print("Encerrando o simulador")
            break
        elif escolha in ['1', '2', '3', '4']:
            CONFIG['LARGURA_BANDA'] = 1e6
            CONFIG['MU_REDE'] = CONFIG['LARGURA_BANDA'] / CONFIG['TAMANHO_LOG_AVG']
            executar_simulacao(escolha)
        elif escolha == '5':
            print("\nEscolha o perfil de Largura de Banda:")
            print("1 - Baixa (100 Kbps - Possível gargalo)")
            print("2 - Média (1 Mbps - Padrão)")
            print("3 - Alta  (10 Mbps - Rede folgada)")
            print("4 - Rodada Comparativa Automática (Sobrepor gráficos 1, 2 e 3)")
            bw_escolha = input("Digite a opção (1, 2, 3 ou 4): ").strip()
            
            if bw_escolha == '4':
                executar_simulacao_comparativa()
                continue
                
            if bw_escolha == '1':
                CONFIG['LARGURA_BANDA'] = 100e3
            elif bw_escolha == '3':
                CONFIG['LARGURA_BANDA'] = 10e6
            else:
                CONFIG['LARGURA_BANDA'] = 1e6 # Default (Media)
                
            CONFIG['MU_REDE'] = CONFIG['LARGURA_BANDA'] / CONFIG['TAMANHO_LOG_AVG']
            executar_simulacao('5')
        else:
            print("Opção inválida! Por favor, tente novamente")

if __name__ == "__main__":
    main()
