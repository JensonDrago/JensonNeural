param(
    [string]$jclInput  = "RUTA_A_REEMPLAZAR",
    [string]$jclOutput = ""
)

if (Test-Path $jclInput -PathType Leaf) {
    $jclFiles  = @(Get-Item $jclInput)
    $outputDir = if ($jclOutput) { $jclOutput } else { Split-Path $jclInput -Parent }
} elseif (Test-Path $jclInput -PathType Container) {
    $jclFiles  = Get-ChildItem $jclInput -Filter "*.jcl"
    $outputDir = if ($jclOutput) { $jclOutput } else { Join-Path $jclInput "neo4j_csv" }
} else {
    Write-Error "Ruta no encontrada: $jclInput"; exit 1
}
[void](New-Item -ItemType Directory -Force -Path $outputDir)

$utilDef = @{
    'SORT'     =@{type='UTILITY_SORT';    func='SORT_DATA';       family='SORT';    desc='Ordena y transforma registros'}
    'SYNCSORT' =@{type='UTILITY_SORT';    func='SORT_DATA';       family='SORT';    desc='Ordena y transforma registros'}
    'ICEMAN'   =@{type='UTILITY_SORT';    func='SORT_DATA';       family='SORT';    desc='Ordena y transforma registros (DFSORT)'}
    'ICETOOL'  =@{type='UTILITY_ICETOOL'; func='PROCESS_DATA';    family='ICETOOL'; desc='Herramienta multi-funcion DFSORT'}
    'ICEGENER' =@{type='UTILITY_COPY';    func='COPY_DATA';       family='COPY';    desc='Copia datasets'}
    'IEBGENER' =@{type='UTILITY_COPY';    func='COPY_DATA';       family='COPY';    desc='Copia datasets'}
    'IEBCOPY'  =@{type='UTILITY_COPY';    func='COPY_PDS';        family='COPY';    desc='Copia/comprime PDS'}
    'IDCAMS'   =@{type='UTILITY_VSAM';    func='MANAGE_VSAM';     family='VSAM';    desc='Define/borra/altera VSAM y catalogo'}
    'ADUUMAIN' =@{type='UTILITY_DB2';     func='DB2_UNLOAD';      family='DB2';     desc='Descarga tabla DB2 a dataset'}
    'DSNUTILB' =@{type='UTILITY_DB2';     func='DB2_UTILITY';     family='DB2';     desc='Utilitario DB2 LOAD/UNLOAD/REORG'}
    'DSNTEP2'  =@{type='UTILITY_DB2';     func='DB2_QUERY';       family='DB2';     desc='Ejecuta SQL DB2 en batch'}
    'IKJEFT1A' =@{type='UTILITY_TSO';     func='RUN_TSO';         family='TSO';     desc='Ejecuta comandos TSO/REXX/CLIST'}
    'IKJEFT01' =@{type='UTILITY_TSO';     func='RUN_TSO';         family='TSO';     desc='Ejecuta comandos TSO/REXX/CLIST'}
    'IEFBR14'  =@{type='UTILITY_NOP';     func='ALLOCATE_DD';     family='NOP';     desc='Paso nulo para asignacion de DDs'}
    'DITTO'    =@{type='UTILITY_MISC';    func='PRINT_DATA';      family='MISC';    desc='Imprime/copia datasets'}
    'TRSMAIN'  =@{type='UTILITY_MISC';    func='COMPRESS_DATA';   family='MISC';    desc='Comprime/descomprime TERSE'}
    'AMASPZAP' =@{type='UTILITY_MISC';    func='PATCH_LOAD';      family='MISC';    desc='Parchea modulos load'}
    'IEHPROGM' =@{type='UTILITY_MISC';    func='SCRATCH_DS';      family='MISC';    desc='Borra/renombra datasets'}
    'ACPMAIN'  =@{type='UTILITY_APP_BATCH'; func='IMAGE_COPY';      family='APPLICATION_BATCH'; desc='IMAGE COPY DE LA TABLA'}
}
$utilSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$utilDef.Keys | ForEach-Object { [void]$utilSet.Add($_) }

$nodeMap      = @{}
$nodes        = [System.Collections.Generic.List[object]]::new()
$edges        = [System.Collections.Generic.List[object]]::new()
$jobStepOrder  = [System.Collections.Specialized.OrderedDictionary]::new()
$stepExecMap   = @{}
$stepRunPgmMap  = @{}   # step_id -> programas invocados via IKJEFT1A RUN PROGRAM()
$stepDb2UnloadMap = @{}  # step_id -> {table_owner, table_name, db2_fields[], sysrec_dsn}
$jobCommentMap  = @{}   # job_name -> descripcion extraida de comentarios //* antes del primer EXEC
$stepCommentMap = @{}   # step_id  -> descripcion extraida de comentarios //* antes del EXEC del step

# ═══════════════════════════════════════════════════════════════════════════
# FUNCIONES DE CALCULO DE 5 EJES DE ANALISIS
# ═══════════════════════════════════════════════════════════════════════════

# Genera un numero pseudo-aleatorio determinista basado en una semilla string
function Get-DetHash([string]$seed, [int]$min, [int]$max) {
    $h = 0
    foreach ($c in $seed.ToCharArray()) { $h = ($h * 31 + [int]$c) -band 0x7FFFFFFF }
    return $min + ($h % ($max - $min + 1))
}

# EJE 1: Deuda Tecnica JCL (0-100, mayor = mas deuda)
# Indicadores: sin descripcion de job, sin descripcion de steps, uso de IEFBR14 excesivo,
# muchos datasets sin LRECL, steps sin comentarios, nombres de steps genéricos
function Get-JclDebtScore([string]$jobId, [int]$stepCount, [int]$stepsWithDesc, [int]$iefbr14Count, [int]$datasetsNoLrecl) {
    $score = 0
    # Sin descripcion de job
    if (!$jobCommentMap.ContainsKey($jobId) -or $jobCommentMap[$jobId].Length -lt 5) { $score += 20 }
    # Proporcion de steps sin descripcion
    if ($stepCount -gt 0) {
        $pctSinDesc = [int](100 * ($stepCount - $stepsWithDesc) / $stepCount)
        $score += [int]($pctSinDesc * 0.30)
    }
    # Uso de IEFBR14 (paso nulo, huella de codigo legacy)
    $score += [Math]::Min($iefbr14Count * 5, 20)
    # Datasets sin LRECL en steps de salida
    $score += [Math]::Min($datasetsNoLrecl * 3, 15)
    # Penalizacion base si tiene muchos pasos (JCL monolitico)
    if ($stepCount -gt 20) { $score += 10 }
    elseif ($stepCount -gt 10) { $score += 5 }
    return [Math]::Min($score, 100)
}

# EJE 2: Complejidad JCL (0-100)
# Indicadores: cantidad de steps, utilitarios distintos, programas distintos,
# datasets de entrada + salida, uso de TSO/DB2, encadenamiento
function Get-JclComplexityScore([int]$stepCount, [int]$utilCount, [int]$pgmCount, [int]$dsInCount, [int]$dsOutCount, [bool]$hasDb2, [bool]$hasTso) {
    $score = 0
    # Pasos
    if ($stepCount -ge 20) { $score += 30 }
    elseif ($stepCount -ge 10) { $score += 20 }
    elseif ($stepCount -ge 5)  { $score += 10 }
    else                        { $score += 5 }
    # Utilitarios distintos
    $score += [Math]::Min($utilCount * 5, 20)
    # Programas de negocio
    $score += [Math]::Min($pgmCount * 4, 15)
    # Datasets
    $score += [Math]::Min(($dsInCount + $dsOutCount) * 2, 20)
    # Tecnologias complejas
    if ($hasDb2) { $score += 10 }
    if ($hasTso)  { $score += 5 }
    return [Math]::Min($score, 100)
}

# EJE 3: Valor de Negocio JCL (0-100, mayor = mas critico para el negocio)
# Indicadores: tiene descripcion, accede a DB2, genera muchos datasets de salida,
# es invocado/referenciado, nombre de job sugiere criticidad
function Get-JclBusinessValue([string]$jobId, [int]$stepCount, [int]$pgmCount, [int]$dsOutCount, [bool]$hasDb2, [bool]$hasDesc) {
    $score = 0
    # Descripcion disponible (documentado = valorado por el negocio)
    if ($hasDesc) { $score += 15 }
    # Acceso a DB2 (operaciones transaccionales/reportes)
    if ($hasDb2) { $score += 25 }
    # Genera salidas (produce informacion)
    $score += [Math]::Min($dsOutCount * 5, 20)
    # Tiene programas de negocio
    $score += [Math]::Min($pgmCount * 6, 20)
    # Volumen de pasos (proceso complejo = mayor valor)
    if ($stepCount -ge 10) { $score += 10 }
    elseif ($stepCount -ge 5) { $score += 5 }
    # Nombre sugiere procesos criticos (pagos, cierre, liquidacion)
    $critKeywords = @('PAG','LIQ','CIE','CRE','DEB','EMI','COB','FAC','CON','REM','TRF')
    foreach ($kw in $critKeywords) {
        if ($jobId -like "*$kw*") { $score += 10; break }
    }
    return [Math]::Min($score, 100)
}

# EJE 4: Riesgo de Seguridad JCL (0-100, mayor = mayor riesgo)
# Indicadores: accede DB2 con escritura (LOAD/UNLOAD/UPDATE), datasets con nombres sensibles,
# uso de utilitarios que modifican datasets de produccion, TSO con RUN PROGRAM
function Get-JclSecurityRisk([string]$jobId, [bool]$hasDb2Write, [bool]$hasDb2Read, [int]$dsOutCount, [bool]$hasTso, [bool]$hasAduumain, [System.Collections.Generic.HashSet[string]]$dsOutNames) {
    $score = 0
    # Escritura en DB2
    if ($hasDb2Write) { $score += 30 }
    elseif ($hasDb2Read) { $score += 10 }
    # Muchos datasets de salida (potencial exfiltracion/modificacion)
    $score += [Math]::Min($dsOutCount * 3, 15)
    # Uso de TSO con RUN PROGRAM (ejecucion dinamica)
    if ($hasTso) { $score += 15 }
    # ADUUMAIN (descarga de tablas DB2 completas)
    if ($hasAduumain) { $score += 20 }
    # Nombres de datasets con datos sensibles
    $sensitivePatterns = @('CRED','CUEN','CLTE','PASP','DOCU','CONT','SALD','IMPU','CLIE','TARJ','PASS','SECU','CRYPT')
    foreach ($dsn in $dsOutNames) {
        foreach ($pat in $sensitivePatterns) {
            if ($dsn -like "*$pat*") { $score += 5; break }
        }
    }
    return [Math]::Min($score, 100)
}

# EJE 5: Tiempo de Procesamiento (simulado/ficticio, en segundos)
# Basado en: cantidad de steps, uso de DB2, cantidad de datasets, tamaño estimado
function Get-JclProcessingTime([string]$jobId, [int]$stepCount, [bool]$hasDb2, [int]$dsCount, [int]$pgmCount) {
    # Base determinista + variacion por caracteristicas
    $base = Get-DetHash $jobId 60 300
    $extra = $stepCount * (Get-DetHash "$jobId.step" 8 25)
    if ($hasDb2) { $extra += Get-DetHash "$jobId.db2" 30 120 }
    $extra += $dsCount * (Get-DetHash "$jobId.ds" 2 8)
    $extra += $pgmCount * (Get-DetHash "$jobId.pgm" 5 20)
    $total = $base + $extra
    return $total
}

# Calcula nivel textual de riesgo/deuda/complejidad
function Get-Level([int]$score) {
    if ($score -ge 75) { return 'ALTO' }
    elseif ($score -ge 50) { return 'MEDIO' }
    elseif ($score -ge 25) { return 'BAJO' }
    else { return 'MUY_BAJO' }
}

function Upsert-Node([string]$id,[string]$type,[string]$lrecl="",[string]$func="",[string]$desc="",[string]$family="") {
    if ($nodeMap.ContainsKey($id)) {
        $ex = $nodeMap[$id]
        if ($type -eq "DATASET_OUTPUT" -and $ex.type -in @("DATASET_INPUT","DATASET")) { $ex.type=$type }
        if ($lrecl  -and !$ex.lrecl)  { $ex.lrecl=$lrecl }
        if ($func   -and !$ex.func)   { $ex.func=$func }
        if ($family -and !$ex.family) { $ex.family=$family }
    } else {
        $o=[PSCustomObject]@{id=$id;type=$type;label=$id;lrecl=$lrecl;func=$func;desc=$desc;family=$family}
        $nodeMap[$id]=$o; $nodes.Add($o)
    }
}
function Add-Edge([string]$s,[string]$t,[string]$r) {
    $edges.Add([PSCustomObject]@{source=$s;target=$t;relation=$r})
}

foreach ($file in $jclFiles) {
    $raw = Get-Content $file.FullName

    $jobName=$null
    foreach ($ln in $raw) {
        if ($ln -match '^//([A-Z0-9#$@]{1,8})\s+JOB[\s,]') { $jobName=$Matches[1].ToUpper(); break }
    }
    if (!$jobName) { $jobName=[IO.Path]::GetFileNameWithoutExtension($file.Name).ToUpper() }
    Upsert-Node $jobName "JOB"

    $inlineDD=@{}; $inData=$false; $dataName=$null; $curStepFL=$null
    $dataBuf=[System.Text.StringBuilder]::new()
    $pendingBuf=[System.Collections.Generic.List[string]]::new()
    $foundFirstExec=$false

    $stmts=[System.Collections.Generic.List[string]]::new()
    $buf=$null

    foreach ($rawLine in $raw) {
        $ln = if ($rawLine.Length -gt 72) { $rawLine.Substring(0,72) } else { $rawLine }

        if ($inData) {
            if ($ln -match '^/\*' -or ($ln -match '^//' -and $ln -notmatch '^//\s')) {
                if ($dataName -and $curStepFL) { $inlineDD["${curStepFL}:${dataName}"]=$dataBuf.ToString() }
                $dataName=$null; $inData=$false; [void]$dataBuf.Clear()
            } else { [void]$dataBuf.AppendLine($ln); continue }
        }

        if ($ln -match '^//\*' -or $ln -match '^\s*$' -or $ln -match '^/\*') {
            if ($buf) { $stmts.Add($buf); $buf=$null }
            if ($ln -match '^//\*') {
                $ct = ($ln.Substring(3) -replace '\s+\d{8}\s*$','').Trim().Trim('*').Trim('-').Trim()
                if ($ct.Length -gt 3 -and $ct -notmatch '^[*\-=\s]+$' -and $ct -notmatch '^%%' -and
                    $ct -notmatch '^[A-Z0-9#$@]{1,8}\s+(EXEC|DD|PROC)\s') {
                    [void]$pendingBuf.Add($ct)
                }
            }
            continue
        }
        if ($ln -notmatch '^//') { continue }
        # Rastrear step actual para clave STEP:DDNAME en inlineDD
        if ($ln -match '^//([A-Z0-9#$@]{1,8})\s+EXEC\s') {
            $curStepFL=$Matches[1].ToUpper()
            if ($pendingBuf.Count -gt 0) {
                $cval = $pendingBuf -join ' | '
                if (!$foundFirstExec) { $jobCommentMap[$jobName] = $cval }
                else { $stepCommentMap["$jobName.$curStepFL"] = $cval }
                [void]$pendingBuf.Clear()
            }
            $foundFirstExec = $true
        }
        if ($foundFirstExec -and $ln -match '^//[A-Z0-9#$@]{1,8}\s+DD\b') { [void]$pendingBuf.Clear() }
        if ($ln -match '^//\s+(IF|ELSE|ENDIF|SET)\b') {
            if ($buf) { $stmts.Add($buf); $buf=$null }; continue
        }
        if ($ln -match '^//([A-Z0-9#$@]{1,8})\s+DD\s+\*') {
            $dataName=$Matches[1].ToUpper(); $inData=$true; [void]$dataBuf.Clear()
            if ($buf) { $stmts.Add($buf) }
            $buf=$ln; $stmts.Add($buf); $buf=$null; continue
        }
        if ($ln -match '^//([A-Z0-9#$@]{1,8})\s') {
            if ($buf) { $stmts.Add($buf) }; $buf=$ln
        } elseif ($ln -match '^//\s') {
            if ($buf) { $buf += ' ' + $ln.Substring(2).Trim() }
        }
    }
    # Flush: si el JCL termina con inline data sin cerrar (ej: SYSTSIN al final del archivo)
    if ($inData -and $dataName -and $curStepFL) {
        $inlineDD["${curStepFL}:${dataName}"] = $dataBuf.ToString()
    }
    if ($buf) { $stmts.Add($buf) }

    $curStep=$null; $curPgm=$null
    foreach ($stmt in $stmts) {
        if ($stmt.Length -lt 2) { continue }
        $c = $stmt.Substring(2)
        if (!($c -match '^([A-Z0-9#$@]{1,8})\s+([A-Z0-9]+)\s+(.+)$')) { continue }
        $sn=$Matches[1].ToUpper(); $op=$Matches[2].ToUpper(); $opr=$Matches[3]

        if ($op -eq 'EXEC') {
            if ($opr -match 'PGM=([A-Z0-9#$@]+)') {
                $pgm=$Matches[1].ToUpper(); $curStep=$sn; $curPgm=$pgm
                $sid="$jobName.$sn"
                Upsert-Node $sid "STEP"
                if (!$jobStepOrder.Contains($jobName)) {
                    $jobStepOrder[$jobName]=[System.Collections.Generic.List[string]]::new()
                }
                if (!$jobStepOrder[$jobName].Contains($sid)) { $jobStepOrder[$jobName].Add($sid) }

                if ($utilDef.ContainsKey($pgm)) {
                    $ud=$utilDef[$pgm]
                    $actType=$ud.func
                    # Detectar RUN PROGRAM(xxx) en SYSTSIN (TSO batch DB2)
                    if ($pgm -in @('IKJEFT1A','IKJEFT01') -and $inlineDD.ContainsKey("${sn}:SYSTSIN")) {
                        $tsin=$inlineDD["${sn}:SYSTSIN"]
                        $runPgmMatches=[regex]::Matches($tsin,'RUN\s+PROGRAM\(([A-Z0-9#$@]+)\)')
                        foreach ($rp in $runPgmMatches) {
                            $calledPgm=$rp.Groups[1].Value.ToUpper()
                            Upsert-Node $calledPgm "PROGRAM" "" "BUSINESS_LOGIC" "Programa invocado via RUN PROGRAM desde TSO batch" ""
                            Add-Edge $sid $calledPgm "EXECUTES_PROGRAM"
                            Add-Edge $jobName $calledPgm "CALLS_PGM"
                            if (!$stepRunPgmMap.ContainsKey($sid)) {
                                $stepRunPgmMap[$sid]=[System.Collections.Generic.List[string]]::new()
                            }
                            [void]$stepRunPgmMap[$sid].Add($calledPgm)
                        }
                    }
                    if ($pgm -eq 'ICETOOL' -and $inlineDD.ContainsKey("${sn}:TOOLIN")) {
                        $tl=$inlineDD["${sn}:TOOLIN"]
                        if ($tl -match '\bCOUNT\b')    { $actType='ICETOOL_COUNT' }
                        elseif ($tl -match '\bSELECT\b'){ $actType='ICETOOL_SELECT' }
                        elseif ($tl -match '\bCOPY\b')  { $actType='ICETOOL_COPY' }
                        elseif ($tl -match '\bSORT\b')  { $actType='ICETOOL_SORT' }
                        elseif ($tl -match '\bDISPLAY\b'){ $actType='ICETOOL_DISPLAY' }
                    }
                    if ($pgm -eq 'DSNUTILB' -and $inlineDD.ContainsKey("${sn}:SYSIN")) {
                        $sy=$inlineDD["${sn}:SYSIN"]
                        if ($sy -match '\bUNLOAD\b')    { $actType='DB2_UNLOAD' }
                        elseif ($sy -match '\bLOAD\b')  { $actType='DB2_LOAD' }
                        elseif ($sy -match '\bREORG\b') { $actType='DB2_REORG' }
                        elseif ($sy -match '\bCOPY\b')  { $actType='DB2_COPY' }
                        elseif ($sy -match '\bRUNSTATS\b'){ $actType='DB2_RUNSTATS' }
                        elseif ($sy -match '\bCHECK\b') { $actType='DB2_CHECK' }
                    }
                    if ($pgm -eq 'ADUUMAIN' -and $inlineDD.ContainsKey("${sn}:SYSIN")) {
                        $sy = $inlineDD["${sn}:SYSIN"]
                        if ($sy -match '\bUNLOAD\b') {
                            $db2Owner = ''; $db2Table = ''
                            $fromM = [regex]::Match($sy, '(?i)\bFROM\s+([A-Z0-9#$@]+)\.([A-Z0-9#$@]+)')
                            if ($fromM.Success) { $db2Owner=$fromM.Groups[1].Value.ToUpper(); $db2Table=$fromM.Groups[2].Value.ToUpper() }
                            $selM = [regex]::Match($sy, '(?is)\bSELECT\b(.+?)\bFROM\b')
                            $db2Fields = @()
                            if ($selM.Success) {
                                $db2Fields = ($selM.Groups[1].Value -split '[,\s\r\n]+') |
                                    Where-Object { $_ -match '^[A-Z0-9_#$@]+$' -and $_.Length -gt 1 }
                            }
                            $stepDb2UnloadMap[$sid] = @{table_owner=$db2Owner; table_name=$db2Table; db2_fields=$db2Fields; sysrec_dsn=''}
                        }
                    }
                    Upsert-Node $pgm $ud.type "" $actType $ud.desc $ud.family
                    Add-Edge $sid $pgm "EXECUTES_$actType"
                    Add-Edge $jobName $sid "HAS_STEP_$actType"
                    $stepExecMap[$sid]=@{exec_type='UTILITY';activity_type=$actType;target=$pgm}
                } else {
                    Upsert-Node $pgm "PROGRAM" "" "BUSINESS_LOGIC" "Programa custom del aplicativo" ""
                    Add-Edge $sid $pgm "EXECUTES_PROGRAM"
                    Add-Edge $jobName $pgm "CALLS_PGM"
                    Add-Edge $jobName $sid "HAS_STEP"
                    $stepExecMap[$sid]=@{exec_type='PROGRAM';activity_type='BUSINESS_LOGIC';target=$pgm}
                }
            } elseif ($opr -match 'PROC=([A-Z0-9#$@]+)') {
                $pr=$Matches[1].ToUpper(); $curStep=$sn; $curPgm=$null
                $sid="$jobName.$sn"
                Upsert-Node $sid "STEP"; Upsert-Node $pr "PROC"
                if (!$jobStepOrder.Contains($jobName)) { $jobStepOrder[$jobName]=[System.Collections.Generic.List[string]]::new() }
                if (!$jobStepOrder[$jobName].Contains($sid)) { $jobStepOrder[$jobName].Add($sid) }
                Add-Edge $jobName $sid "HAS_STEP"; Add-Edge $sid $pr "CALLS_PROC"; Add-Edge $jobName $pr "USES_PROC"
                $stepExecMap[$sid]=@{exec_type='PROC';activity_type='PROC_CALL';target=$pr}
            }
        }
        elseif ($op -eq 'DD') {
            $dsnMs=[regex]::Matches($opr,'DSN=([^\s,\)]+)')
            foreach ($dm in $dsnMs) {
                $dsn=$dm.Groups[1].Value.ToUpper()
                if ($dsn.Length -lt 3 -or $dsn -eq '*') { continue }
                $dt="DATASET"
                if    ($opr -match 'DISP=\(?\s*(SHR|OLD)\b') { $dt="DATASET_INPUT"  }
                elseif ($opr -match 'DISP=\(?\s*(NEW|MOD)\b') { $dt="DATASET_OUTPUT" }
                $lr=""
                if ($dt -eq "DATASET_OUTPUT" -and $opr -match 'LRECL=(\d+)') { $lr=$Matches[1] }
                Upsert-Node $dsn $dt $lr
                if ($curStep) {
                    $sid="$jobName.$curStep"
                    if      ($dt -eq "DATASET_INPUT")  { Add-Edge $dsn $sid "READS_FROM" }
                    elseif  ($dt -eq "DATASET_OUTPUT") {
                        $wrel="WRITES_TO"
                        if ($curPgm -in @("SORT","SYNCSORT","ICEMAN") -and $sn -match '^(SORTOUT|OUT)') { $wrel="SORT_OUTPUT" }
                        if ($curPgm -in @("ADUUMAIN","DSNUTILB")) { $wrel="DB2_EXTRACT_OUTPUT" }
                        Add-Edge $sid $dsn $wrel
                        if ($curPgm -eq 'ADUUMAIN' -and $sn -eq 'SYSREC') {
                            $sid2="$jobName.$curStep"
                            if ($stepDb2UnloadMap.ContainsKey($sid2)) { $stepDb2UnloadMap[$sid2].sysrec_dsn = $dsn }
                            else { $stepDb2UnloadMap[$sid2] = @{table_owner='';table_name='';db2_fields=@();sysrec_dsn=$dsn} }
                        }
                    } else { Add-Edge $sid $dsn "USES_DSN" }
                }
            }
        }
    }
}

# ═══════════════════════════════════════════════════════════════════════════
# INDICES DE LECTURA/ESCRITURA POR STEP (necesarios para ejes de analisis)
# ═══════════════════════════════════════════════════════════════════════════
$stepReadsMap  = @{}   # step_id -> list<dsn>
$stepWritesMap = @{}   # step_id -> list<dsn>
foreach ($e in $edges) {
    if ($e.relation -eq 'READS_FROM') {
        $sid = $e.target; $dsn = $e.source
        if (!$stepReadsMap.ContainsKey($sid))  { $stepReadsMap[$sid]  = [System.Collections.Generic.List[string]]::new() }
        [void]$stepReadsMap[$sid].Add($dsn)
    }
    if ($e.relation -in @('WRITES_TO','SORT_OUTPUT','DB2_EXTRACT_OUTPUT')) {
        $sid = $e.source; $dsn = $e.target
        if (!$stepWritesMap.ContainsKey($sid)) { $stepWritesMap[$sid] = [System.Collections.Generic.List[string]]::new() }
        [void]$stepWritesMap[$sid].Add($dsn)
    }
}

# ═══════════════════════════════════════════════════════════════════════════
# GENERAR LOS 13 ARCHIVOS CSV
# ═══════════════════════════════════════════════════════════════════════════
$W = { param($path,$lines) [System.IO.File]::WriteAllLines($path,$lines) }
$jobNodes = @($nodes | Where-Object { $_.type -eq 'JOB' })

# 1. job.csv  (incluye ejes de analisis por job)
$L=[System.Collections.Generic.List[string]]::new()
$L.Add('job_id,job_name,technical_debt_score,technical_debt_level,complexity_score,complexity_level,business_value_score,business_value_level,security_risk_score,security_risk_level,processing_time_sec')
foreach ($j in $jobNodes) {
    $jid = $j.id
    $jSteps = if ($jobStepOrder.Contains($jid)) { $jobStepOrder[$jid] } else { @() }
    $jStepCount = $jSteps.Count

    # Calcular metricas agregadas del job
    $jIefbr14 = 0; $jUtilSet = [System.Collections.Generic.HashSet[string]]::new()
    $jPgmSet  = [System.Collections.Generic.HashSet[string]]::new()
    $jDsIn    = [System.Collections.Generic.HashSet[string]]::new()
    $jDsOut   = [System.Collections.Generic.HashSet[string]]::new()
    $jHasDb2  = $false; $jHasTso = $false; $jHasAdu = $false
    $jStepsWithDesc = 0; $jDsNoLrecl = 0

    foreach ($sid in $jSteps) {
        $et = 'UNKNOWN'; $at = 'UNKNOWN'; $tgt = ''
        if ($stepExecMap.ContainsKey($sid)) { $et=$stepExecMap[$sid].exec_type; $at=$stepExecMap[$sid].activity_type; $tgt=$stepExecMap[$sid].target }
        if ($et -eq 'UTILITY') { [void]$jUtilSet.Add($tgt) }
        elseif ($et -eq 'PROGRAM') { [void]$jPgmSet.Add($tgt) }
        if ($tgt -eq 'IEFBR14') { $jIefbr14++ }
        if ($at -like 'DB2*' -or $tgt -in @('ADUUMAIN','DSNUTILB','DSNTEP2')) { $jHasDb2 = $true }
        if ($tgt -in @('IKJEFT1A','IKJEFT01')) { $jHasTso = $true }
        if ($tgt -eq 'ADUUMAIN') { $jHasAdu = $true }
        if ($stepCommentMap.ContainsKey($sid) -and $stepCommentMap[$sid].Length -gt 5) { $jStepsWithDesc++ }
        if ($stepReadsMap.ContainsKey($sid))  { foreach ($d in $stepReadsMap[$sid])  { [void]$jDsIn.Add($d) } }
        if ($stepWritesMap.ContainsKey($sid)) { foreach ($d in $stepWritesMap[$sid]) { [void]$jDsOut.Add($d) } }
    }
    # Datasets de salida sin LRECL
    foreach ($dsn in $jDsOut) {
        if ($nodeMap.ContainsKey($dsn) -and !$nodeMap[$dsn].lrecl) { $jDsNoLrecl++ }
    }

    $jHasDb2Write = ($jSteps | Where-Object {
        $stepExecMap.ContainsKey($_) -and $stepExecMap[$_].activity_type -in @('DB2_LOAD','DB2_REORG','DB2_COPY','DB2_UNLOAD')
    }).Count -gt 0
    $jHasDesc = $jobCommentMap.ContainsKey($jid) -and $jobCommentMap[$jid].Length -gt 5

    $sc_debt  = Get-JclDebtScore $jid $jStepCount $jStepsWithDesc $jIefbr14 $jDsNoLrecl
    $sc_comp  = Get-JclComplexityScore $jStepCount $jUtilSet.Count $jPgmSet.Count $jDsIn.Count $jDsOut.Count $jHasDb2 $jHasTso
    $sc_bv    = Get-JclBusinessValue $jid $jStepCount $jPgmSet.Count $jDsOut.Count $jHasDb2 $jHasDesc
    $sc_sec   = Get-JclSecurityRisk $jid $jHasDb2Write $jHasDb2 $jDsOut.Count $jHasTso $jHasAdu $jDsOut
    $sc_time  = Get-JclProcessingTime $jid $jStepCount $jHasDb2 ($jDsIn.Count + $jDsOut.Count) $jPgmSet.Count

    $L.Add("$jid,$jid,$sc_debt,$(Get-Level $sc_debt),$sc_comp,$(Get-Level $sc_comp),$sc_bv,$(Get-Level $sc_bv),$sc_sec,$(Get-Level $sc_sec),$sc_time")
}
& $W "$outputDir\job.csv" $L

# 2. job_has_step.csv  (relacion directa Job->Step, nodo Jcl eliminado)
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('job_id,step_id')
foreach ($job in $jobStepOrder.Keys) { foreach ($sid in $jobStepOrder[$job]) { $L.Add("$job,$sid") } }
& $W "$outputDir\job_has_step.csv" $L

# 5. step.csv  (incluye ejes de analisis por step)
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('step_id,job_id,step_name,sequence_number,exec_type,activity_type,complexity_score,complexity_level,business_value_score,business_value_level,security_risk_score,security_risk_level,technical_debt_score,technical_debt_level,processing_time_sec')
foreach ($job in $jobStepOrder.Keys) {
    $seq=10
    foreach ($sid in $jobStepOrder[$job]) {
        $sname=$sid -replace "^$([regex]::Escape($job))\.",''
        $et='UNKNOWN'; $at='UNKNOWN'; $tgt=''
        if ($stepExecMap.ContainsKey($sid)) { $et=$stepExecMap[$sid].exec_type; $at=$stepExecMap[$sid].activity_type; $tgt=$stepExecMap[$sid].target }

        # Calcular ejes por step
        $sReads  = if ($stepReadsMap.ContainsKey($sid))  { $stepReadsMap[$sid].Count }  else { 0 }
        $sWrites = if ($stepWritesMap.ContainsKey($sid)) { $stepWritesMap[$sid].Count } else { 0 }
        $sHasDb2 = ($at -like 'DB2*' -or $et -eq 'UTILITY' -and $tgt -in @('ADUUMAIN','DSNUTILB','DSNTEP2'))
        $sHasTso = ($tgt -in @('IKJEFT1A','IKJEFT01'))
        $sIsIefbr14 = ($tgt -eq 'IEFBR14')
        $sHasAdu = ($tgt -eq 'ADUUMAIN')

        # Complejidad del step
        $sc_comp = 0
        if ($et -eq 'UTILITY') { $sc_comp += 10 } else { $sc_comp += 20 }
        $sc_comp += [Math]::Min(($sReads + $sWrites) * 5, 30)
        if ($sHasDb2) { $sc_comp += 20 }
        if ($sHasTso)  { $sc_comp += 15 }
        if ($stepRunPgmMap.ContainsKey($sid) -and $stepRunPgmMap[$sid].Count -gt 0) { $sc_comp += 10 }
        $sc_comp = [Math]::Min($sc_comp, 100)

        # Valor de negocio del step
        $sc_bv = 0
        if ($sHasDb2) { $sc_bv += 30 }
        if ($et -eq 'PROGRAM') { $sc_bv += 25 }
        $sc_bv += [Math]::Min($sWrites * 8, 20)
        $hasStepDesc = $stepCommentMap.ContainsKey($sid) -and $stepCommentMap[$sid].Length -gt 5
        if ($hasStepDesc) { $sc_bv += 10 }
        $sc_bv = [Math]::Min($sc_bv, 100)

        # Riesgo seguridad del step
        $sc_sec = 0
        if ($sHasAdu) { $sc_sec += 40 }
        if ($at -in @('DB2_LOAD','DB2_UNLOAD','DB2_COPY','DB2_REORG')) { $sc_sec += 20 }
        if ($sHasTso) { $sc_sec += 15 }
        $sc_sec += [Math]::Min($sWrites * 5, 20)
        $sc_sec = [Math]::Min($sc_sec, 100)

        # Deuda tecnica del step
        $sc_debt = 0
        if (!$hasStepDesc) { $sc_debt += 20 }
        if ($sIsIefbr14) { $sc_debt += 25 }
        if ($sName -match '^(STEP|PASOS?)\d+$') { $sc_debt += 15 }  # nombre generico
        if ($et -eq 'UNKNOWN') { $sc_debt += 20 }
        $sc_debt = [Math]::Min($sc_debt, 100)

        # Tiempo de procesamiento (simulado)
        $sc_time = Get-DetHash $sid 5 60
        if ($sHasDb2) { $sc_time += Get-DetHash "$sid.db2" 10 60 }
        $sc_time += ($sReads + $sWrites) * (Get-DetHash "$sid.ds" 1 5)
        if ($et -eq 'PROGRAM') { $sc_time += Get-DetHash "$sid.pgm" 5 30 }

        $L.Add("$sid,$job,$sname,$seq,$et,$at,$sc_comp,$(Get-Level $sc_comp),$sc_bv,$(Get-Level $sc_bv),$sc_sec,$(Get-Level $sc_sec),$sc_debt,$(Get-Level $sc_debt),$sc_time")
        $seq+=10
    }
}
& $W "$outputDir\step.csv" $L

# 6. step_next_step.csv
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('from_step_id,to_step_id')
foreach ($job in $jobStepOrder.Keys) {
    $steps=$jobStepOrder[$job]
    for ($i=0;$i -lt ($steps.Count-1);$i++) { $L.Add("$($steps[$i]),$($steps[$i+1])") }
}
& $W "$outputDir\step_next_step.csv" $L

# 7 & 8. step_executes_utility / step_executes_program
$usedUtils=[System.Collections.Generic.HashSet[string]]::new()
$usedPgms=[System.Collections.Generic.HashSet[string]]::new()
$Lu=[System.Collections.Generic.List[string]]::new(); $Lu.Add('step_id,utility_id')
$Lp=[System.Collections.Generic.List[string]]::new(); $Lp.Add('step_id,program_id')
foreach ($sid in $stepExecMap.Keys) {
    $info=$stepExecMap[$sid]
    if ($info.exec_type -eq 'UTILITY') { $Lu.Add("$sid,UTIL_$($info.target)"); [void]$usedUtils.Add($info.target) }
    elseif ($info.exec_type -eq 'PROGRAM') { $Lp.Add("$sid,$($info.target)"); [void]$usedPgms.Add($info.target) }
}
# Agregar programas invocados via RUN PROGRAM(x) dentro de SYSTSIN
foreach ($sid in $stepRunPgmMap.Keys) {
    foreach ($calledPgm in $stepRunPgmMap[$sid]) {
        $Lp.Add("$sid,$calledPgm")
        [void]$usedPgms.Add($calledPgm)
    }
}
& $W "$outputDir\step_executes_utility.csv" $Lu
& $W "$outputDir\step_executes_program.csv" $Lp

# 9. step_reads_dataset.csv
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('step_id,dataset_id')
$seen=[System.Collections.Generic.HashSet[string]]::new()
foreach ($e in $edges) {
    if ($e.relation -eq 'READS_FROM') {
        $key="$($e.target)|$($e.source)"
        if ($seen.Add($key)) { $L.Add('"'+$e.target+'","'+$e.source+'"') }
    }
}
& $W "$outputDir\step_reads_dataset.csv" $L

# 10. step_writes_dataset.csv
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('step_id,dataset_id')
$seen=[System.Collections.Generic.HashSet[string]]::new()
foreach ($e in $edges) {
    if ($e.relation -in @('WRITES_TO','SORT_OUTPUT','DB2_EXTRACT_OUTPUT')) {
        $key="$($e.source)|$($e.target)"
        if ($seen.Add($key)) { $L.Add('"'+$e.source+'","'+$e.target+'"') }
    }
}
& $W "$outputDir\step_writes_dataset.csv" $L

# 11. utility.csv
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('utility_id,name,utility_family')
foreach ($u in ($usedUtils | Sort-Object)) {
    $fam=if($utilDef.ContainsKey($u)){$utilDef[$u].family}else{'MISC'}
    $L.Add("UTIL_$u,$u,$fam")
}
& $W "$outputDir\utility.csv" $L

# 12. program.csv
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('program_id,name,program_type')
foreach ($p in ($usedPgms | Sort-Object)) { $L.Add("$p,$p,CUSTOM_PROGRAM") }
& $W "$outputDir\program.csv" $L

# 13. dataset.csv
$L=[System.Collections.Generic.List[string]]::new(); $L.Add('dataset_id,dsn_name,dataset_type,lrecl')
$dsNodes=$nodes | Where-Object { $_.type -in @('DATASET_INPUT','DATASET_OUTPUT','DATASET') }
foreach ($d in $dsNodes) {
    $dsn=$d.id -replace '"',''
    $L.Add('"'+$dsn+'","'+$dsn+'","'+$d.type+'","'+$d.lrecl+'"')
}
& $W "$outputDir\dataset.csv" $L

# ═══════════════════════════════════════════════════════════════════════════
# GENERAR RAG JSONL (rag_jcl.jsonl)
# ═══════════════════════════════════════════════════════════════════════════
function Json-Str([string]$s) { $s -replace '\\','\\' -replace '"','\"' }

# Los indices stepReadsMap / stepWritesMap ya fueron construidos antes de los CSVs.
# Construir indices adicionales por dataset
$dsReadByMap    = @{}  # dsn -> list<step_id> que leen
$dsWrittenByMap = @{}  # dsn -> list<step_id> que escriben
foreach ($sid in $stepReadsMap.Keys)  { foreach ($dsn in $stepReadsMap[$sid])  { if (!$dsReadByMap.ContainsKey($dsn))    { $dsReadByMap[$dsn]    = [System.Collections.Generic.List[string]]::new() }; [void]$dsReadByMap[$dsn].Add($sid) } }
foreach ($sid in $stepWritesMap.Keys) { foreach ($dsn in $stepWritesMap[$sid]) { if (!$dsWrittenByMap.ContainsKey($dsn)) { $dsWrittenByMap[$dsn] = [System.Collections.Generic.List[string]]::new() }; [void]$dsWrittenByMap[$dsn].Add($sid) } }

$ragLines = [System.Collections.Generic.List[string]]::new()

# Índice inverso: job -> set de jobs que leen/escriben cada dataset
$dsUsedByJobsMap = @{}   # dsn -> HashSet<job_id>
foreach ($sid in $stepReadsMap.Keys) {
    $jobN = ($sid -split '\.')[0]
    foreach ($dsn in $stepReadsMap[$sid]) {
        if (!$dsUsedByJobsMap.ContainsKey($dsn)) { $dsUsedByJobsMap[$dsn]=[System.Collections.Generic.HashSet[string]]::new() }
        [void]$dsUsedByJobsMap[$dsn].Add($jobN)
    }
}
foreach ($sid in $stepWritesMap.Keys) {
    $jobN = ($sid -split '\.')[0]
    foreach ($dsn in $stepWritesMap[$sid]) {
        if (!$dsUsedByJobsMap.ContainsKey($dsn)) { $dsUsedByJobsMap[$dsn]=[System.Collections.Generic.HashSet[string]]::new() }
        [void]$dsUsedByJobsMap[$dsn].Add($jobN)
    }
}

# — Documentos JCL_JOB —
foreach ($job in $jobStepOrder.Keys) {
    $steps     = $jobStepOrder[$job]
    $stepCount = $steps.Count

    $utilNames  = [System.Collections.Generic.HashSet[string]]::new()
    $pgmNames   = [System.Collections.Generic.HashSet[string]]::new()
    $tsoPgms    = [System.Collections.Generic.HashSet[string]]::new()
    $dsIn2      = [System.Collections.Generic.HashSet[string]]::new()
    $dsOut2     = [System.Collections.Generic.HashSet[string]]::new()
    $stepDetails= [System.Collections.Generic.List[string]]::new()

    foreach ($sid in $steps) {
        $sname2 = $sid -replace "^$([regex]::Escape($job))\.",''
        $et2 = 'UNKNOWN'; $at2 = 'UNKNOWN'; $tgt2 = ''
        if ($stepExecMap.ContainsKey($sid)) {
            $et2=$stepExecMap[$sid].exec_type; $at2=$stepExecMap[$sid].activity_type; $tgt2=$stepExecMap[$sid].target
            if ($et2 -eq 'UTILITY') { [void]$utilNames.Add($tgt2) }
            elseif ($et2 -eq 'PROGRAM') { [void]$pgmNames.Add($tgt2) }
        }
        if ($stepRunPgmMap.ContainsKey($sid)) { foreach ($p in $stepRunPgmMap[$sid]) { [void]$tsoPgms.Add($p); [void]$pgmNames.Add($p) } }
        if ($stepReadsMap.ContainsKey($sid))  { foreach ($d in $stepReadsMap[$sid])  { [void]$dsIn2.Add($d) } }
        if ($stepWritesMap.ContainsKey($sid)) { foreach ($d in $stepWritesMap[$sid]) { [void]$dsOut2.Add($d) } }

        $tsoInfo = if ($stepRunPgmMap.ContainsKey($sid)) { ", invoca via TSO: $($stepRunPgmMap[$sid] -join '+')" } else { "" }
        $stepDetails.Add('"' + (Json-Str $sname2) + ':' + $et2 + ':' + (Json-Str $tgt2) + ':' + $at2 + $tsoInfo + '"')
    }

    $dsAll     = [System.Collections.Generic.HashSet[string]]::new($dsIn2); foreach ($x in $dsOut2) { [void]$dsAll.Add($x) }

    $utilArr   = '[' + (($utilNames  | Sort-Object | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $pgmArr    = '[' + (($pgmNames   | Sort-Object | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $tsoArr    = '[' + (($tsoPgms    | Sort-Object | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $dsInArr   = '[' + (($dsIn2      | Sort-Object | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $dsOutArr  = '[' + (($dsOut2     | Sort-Object | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $dsAllArr  = '[' + (($dsAll      | Sort-Object | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $stepArr   = '[' + (($steps                    | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $detailArr = '[' + ($stepDetails -join ',') + ']'

    # Texto enriquecido
    $utilText  = if ($utilNames.Count -gt 0)  { " Utilitarios: $($utilNames -join ', ')." } else { "" }
    $pgmText   = if ($pgmNames.Count  -gt 0)  { " Programas de negocio: $($pgmNames -join ', ')." } else { "" }
    $tsoText   = if ($tsoPgms.Count   -gt 0)  { " Programas invocados via TSO RUN PROGRAM: $($tsoPgms -join ', ')." } else { "" }
    $inText    = if ($dsIn2.Count     -gt 0)  { " Lee $($dsIn2.Count) datasets de entrada: $($dsIn2 -join ', ')." } else { "" }
    $outText   = if ($dsOut2.Count    -gt 0)  { " Escribe $($dsOut2.Count) datasets de salida: $($dsOut2 -join ', ')." } else { "" }

    # Narrativa de flujo de pasos
    $flowParts = [System.Collections.Generic.List[string]]::new()
    $seq2 = 10
    foreach ($sid in $steps) {
        $sname2 = $sid -replace "^$([regex]::Escape($job))\.",''
        $et2='UNKNOWN'; $tgt2=''
        if ($stepExecMap.ContainsKey($sid)) { $et2=$stepExecMap[$sid].exec_type; $tgt2=$stepExecMap[$sid].target }
        $tsoSuffix = if ($stepRunPgmMap.ContainsKey($sid)) { " [TSO:$($stepRunPgmMap[$sid] -join ',')]" } else { "" }
        $flowParts.Add("${seq2}:${sname2}(${et2}:${tgt2}${tsoSuffix})")
        $seq2 += 10
    }
    $flowText = " Flujo de pasos: $($flowParts -join ' -> ')."

    $jobDescRaw  = if ($jobCommentMap.ContainsKey($job)) { $jobCommentMap[$job] } else { '' }
    $jobDescText = if ($jobDescRaw) { " Descripcion: $jobDescRaw." } else { '' }
    $jobDescJson = Json-Str $jobDescRaw
    $text = "JOB JCL: $job. Tiene $stepCount pasos de ejecucion.$jobDescText$utilText$pgmText$tsoText$inText$outText$flowText"

    # ── Calcular 5 ejes de analisis para el JOB ──
    $hasDb2Job   = ($utilNames | Where-Object { $_ -in @('ADUUMAIN','DSNUTILB','DSNTEP2') }).Count -gt 0
    $hasTsoJob   = ($utilNames | Where-Object { $_ -in @('IKJEFT1A','IKJEFT01') }).Count -gt 0
    $hasAduJob   = $utilNames.Contains('ADUUMAIN')
    $stepsWithDescJob = ($jobStepOrder[$job] | Where-Object { $stepCommentMap.ContainsKey($_) -and $stepCommentMap[$_].Length -gt 5 }).Count
    $iefbr14Job  = ($jobStepOrder[$job] | Where-Object { $stepExecMap.ContainsKey($_) -and $stepExecMap[$_].target -eq 'IEFBR14' }).Count
    $dsNoLreclJob = ($dsOut2 | Where-Object { $nodeMap.ContainsKey($_) -and !$nodeMap[$_].lrecl }).Count
    $hasDb2WriteJob = ($jobStepOrder[$job] | Where-Object {
        $stepExecMap.ContainsKey($_) -and $stepExecMap[$_].activity_type -in @('DB2_LOAD','DB2_REORG','DB2_COPY','DB2_UNLOAD')
    }).Count -gt 0
    $hasDescJob = $jobDescRaw.Length -gt 5

    $debt_score  = Get-JclDebtScore $job $stepCount $stepsWithDescJob $iefbr14Job $dsNoLreclJob
    $comp_score  = Get-JclComplexityScore $stepCount $utilNames.Count $pgmNames.Count $dsIn2.Count $dsOut2.Count $hasDb2Job $hasTsoJob
    $bv_score    = Get-JclBusinessValue $job $stepCount $pgmNames.Count $dsOut2.Count $hasDb2Job $hasDescJob
    $sec_score   = Get-JclSecurityRisk $job $hasDb2WriteJob $hasDb2Job $dsOut2.Count $hasTsoJob $hasAduJob $dsOut2
    $time_score  = Get-JclProcessingTime $job $stepCount $hasDb2Job ($dsIn2.Count + $dsOut2.Count) $pgmNames.Count

    $debtLvl = Get-Level $debt_score; $compLvl = Get-Level $comp_score
    $bvLvl   = Get-Level $bv_score;   $secLvl  = Get-Level $sec_score

    # Agregar resumen de ejes al texto RAG
    $axisText = " [ANALISIS] Deuda tecnica: $debt_score/100 ($debtLvl). Complejidad: $comp_score/100 ($compLvl). Valor negocio: $bv_score/100 ($bvLvl). Riesgo seguridad: $sec_score/100 ($secLvl). Tiempo procesamiento estimado: ${time_score}s."
    $text += $axisText
    $textJson = Json-Str $text

    $ragLines.Add('{"id":"JCL_JOB_' + $job + '","text":"' + $textJson + '","metadata":{"type":"jcl_job","job_id":"' + $job + '","job_description":"' + $jobDescJson + '","step_count":' + $stepCount + ',"input_count":' + $dsIn2.Count + ',"output_count":' + $dsOut2.Count + ',"utility_names":' + $utilArr + ',"program_names":' + $pgmArr + ',"tso_programs":' + $tsoArr + ',"input_datasets":' + $dsInArr + ',"output_datasets":' + $dsOutArr + ',"dataset_names":' + $dsAllArr + ',"step_ids":' + $stepArr + ',"step_details":' + $detailArr + ',"technical_debt_score":' + $debt_score + ',"technical_debt_level":"' + $debtLvl + '","complexity_score":' + $comp_score + ',"complexity_level":"' + $compLvl + '","business_value_score":' + $bv_score + ',"business_value_level":"' + $bvLvl + '","security_risk_score":' + $sec_score + ',"security_risk_level":"' + $secLvl + '","processing_time_sec":' + $time_score + '}}')
}

# — Documentos JCL_STEP —
foreach ($job in $jobStepOrder.Keys) {
    $seq = 10
    $stepList = $jobStepOrder[$job]
    for ($si = 0; $si -lt $stepList.Count; $si++) {
        $sid   = $stepList[$si]
        $sname = $sid -replace "^$([regex]::Escape($job))\.",''
        $et = 'UNKNOWN'; $at = 'UNKNOWN'; $target = ''
        if ($stepExecMap.ContainsKey($sid)) { $et=$stepExecMap[$sid].exec_type; $at=$stepExecMap[$sid].activity_type; $target=$stepExecMap[$sid].target }

        $readsItems  = if ($stepReadsMap.ContainsKey($sid))  { $stepReadsMap[$sid]  } else { @() }
        $writesItems = if ($stepWritesMap.ContainsKey($sid)) { $stepWritesMap[$sid] } else { @() }
        $rItems  = ($readsItems  | ForEach-Object { '"' + (Json-Str $_) + '"' }) -join ','
        $wItems  = ($writesItems | ForEach-Object { '"' + (Json-Str $_) + '"' }) -join ','
        $readsArr  = "[$rItems]"
        $writesArr = "[$wItems]"

        # Programas TSO
        $tsoPgmsStep = if ($stepRunPgmMap.ContainsKey($sid)) { $stepRunPgmMap[$sid] } else { @() }
        $tsoStepArr  = '[' + (($tsoPgmsStep | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'

        # Next step
        $nextStep = if ($si+1 -lt $stepList.Count) { ($stepList[$si+1] -replace "^$([regex]::Escape($job))\.",'') } else { '' }

        # Texto enriquecido
        if ($et -eq 'UTILITY') {
            $text = "Step $sname del JOB $job (posicion $seq). Ejecuta el utilitario $target con funcion $at."
        } elseif ($et -eq 'PROGRAM') {
            $text = "Step $sname del JOB $job (posicion $seq). Ejecuta el programa de negocio $target (actividad: $at)."
        } elseif ($et -eq 'PROC') {
            $text = "Step $sname del JOB $job (posicion $seq). Invoca el procedimiento catalogado $target."
        } else {
            $text = "Step $sname del JOB $job (posicion $seq). Tipo de ejecucion: $et."
        }
        if ($tsoPgmsStep.Count -gt 0) {
            $text += " Invoca via TSO RUN PROGRAM: $($tsoPgmsStep -join ', ')."
        }
        $stepDescRaw  = if ($stepCommentMap.ContainsKey($sid)) { $stepCommentMap[$sid] } else { '' }
        if ($stepDescRaw) { $text += " $stepDescRaw." }

        # DB2 UNLOAD info (ADUUMAIN)
        $db2UnloadJson = 'null'
        if ($stepDb2UnloadMap.ContainsKey($sid)) {
            $u = $stepDb2UnloadMap[$sid]
            $tableFull = if ($u.table_owner -and $u.table_name) { "$($u.table_owner).$($u.table_name)" } else { $u.table_name }
            if ($tableFull) { $text += " Descarga la tabla DB2 $tableFull." }
            if ($u.db2_fields.Count -gt 0) { $text += " Campos: $($u.db2_fields -join ', ')." }
            if ($u.sysrec_dsn)  { $text += " Archivo de salida SYSREC: $($u.sysrec_dsn)." }
            $fArr = '[' + (($u.db2_fields | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
            $db2UnloadJson = '{"db2_table":"' + (Json-Str $u.table_name) + '","db2_owner":"' + (Json-Str $u.table_owner) + '","db2_table_full":"' + (Json-Str $tableFull) + '","db2_fields":' + $fArr + ',"sysrec_dsn":"' + (Json-Str $u.sysrec_dsn) + '"}'
        }

        if ($readsItems.Count -gt 0)  { $text += " Lee $($readsItems.Count) datasets: $($readsItems -join ', ')." }
        if ($writesItems.Count -gt 0) { $text += " Escribe $($writesItems.Count) datasets: $($writesItems -join ', ')." }
        if ($nextStep)                { $text += " Paso siguiente: $nextStep." }

        # ── Calcular 5 ejes de analisis para el STEP ──
        $sHasDb2s  = ($at -like 'DB2*' -or $target -in @('ADUUMAIN','DSNUTILB','DSNTEP2'))
        $sHasTsos  = ($target -in @('IKJEFT1A','IKJEFT01'))
        $sHasAdus  = ($target -eq 'ADUUMAIN')
        $sHasDescs = $stepDescRaw.Length -gt 5

        $sstep_comp = 0
        if ($et -eq 'UTILITY') { $sstep_comp += 10 } else { $sstep_comp += 20 }
        $sstep_comp += [Math]::Min(($readsItems.Count + $writesItems.Count) * 5, 30)
        if ($sHasDb2s) { $sstep_comp += 20 }
        if ($sHasTsos)  { $sstep_comp += 15 }
        if ($tsoPgmsStep.Count -gt 0) { $sstep_comp += 10 }
        $sstep_comp = [Math]::Min($sstep_comp, 100)

        $sstep_bv = 0
        if ($sHasDb2s) { $sstep_bv += 30 }
        if ($et -eq 'PROGRAM') { $sstep_bv += 25 }
        $sstep_bv += [Math]::Min($writesItems.Count * 8, 20)
        if ($sHasDescs) { $sstep_bv += 10 }
        $sstep_bv = [Math]::Min($sstep_bv, 100)

        $sstep_sec = 0
        if ($sHasAdus) { $sstep_sec += 40 }
        if ($at -in @('DB2_LOAD','DB2_UNLOAD','DB2_COPY','DB2_REORG')) { $sstep_sec += 20 }
        if ($sHasTsos) { $sstep_sec += 15 }
        $sstep_sec += [Math]::Min($writesItems.Count * 5, 20)
        $sstep_sec = [Math]::Min($sstep_sec, 100)

        $sstep_debt = 0
        if (!$sHasDescs) { $sstep_debt += 20 }
        if ($target -eq 'IEFBR14') { $sstep_debt += 25 }
        if ($sname -match '^(STEP|PASO)\d+$') { $sstep_debt += 15 }
        if ($et -eq 'UNKNOWN') { $sstep_debt += 20 }
        $sstep_debt = [Math]::Min($sstep_debt, 100)

        $sstep_time = Get-DetHash $sid 5 60
        if ($sHasDb2s) { $sstep_time += Get-DetHash "$sid.db2" 10 60 }
        $sstep_time += ($readsItems.Count + $writesItems.Count) * (Get-DetHash "$sid.ds" 1 5)
        if ($et -eq 'PROGRAM') { $sstep_time += Get-DetHash "$sid.pgm" 5 30 }

        $sCompLvl = Get-Level $sstep_comp; $sBvLvl  = Get-Level $sstep_bv
        $sSecLvl  = Get-Level $sstep_sec;  $sDebtLvl = Get-Level $sstep_debt

        $stepAxisText = " [ANALISIS] Deuda tecnica: $sstep_debt/100 ($sDebtLvl). Complejidad: $sstep_comp/100 ($sCompLvl). Valor negocio: $sstep_bv/100 ($sBvLvl). Riesgo seguridad: $sstep_sec/100 ($sSecLvl). Tiempo procesamiento estimado: ${sstep_time}s."
        $text += $stepAxisText

        $textJson    = Json-Str $text
        $nextJson    = Json-Str $nextStep
        $stepDescJson = Json-Str $stepDescRaw

        $ragLines.Add('{"id":"JCL_STEP_' + (Json-Str $sid) + '","text":"' + $textJson + '","metadata":{"type":"jcl_step","step_id":"' + (Json-Str $sid) + '","step_name":"' + (Json-Str $sname) + '","job_id":"' + (Json-Str $job) + '","step_description":"' + $stepDescJson + '","sequence":' + $seq + ',"exec_type":"' + $et + '","activity_type":"' + $at + '","target":"' + (Json-Str $target) + '","db2_unload":' + $db2UnloadJson + ',"tso_programs":' + $tsoStepArr + ',"reads_datasets":' + $readsArr + ',"writes_datasets":' + $writesArr + ',"reads_count":' + $readsItems.Count + ',"writes_count":' + $writesItems.Count + ',"next_step":"' + $nextJson + '","technical_debt_score":' + $sstep_debt + ',"technical_debt_level":"' + $sDebtLvl + '","complexity_score":' + $sstep_comp + ',"complexity_level":"' + $sCompLvl + '","business_value_score":' + $sstep_bv + ',"business_value_level":"' + $sBvLvl + '","security_risk_score":' + $sstep_sec + ',"security_risk_level":"' + $sSecLvl + '","processing_time_sec":' + $sstep_time + '}}')
        $seq += 10
    }
}

# — Documentos JCL_DATASET —
$dsNodes2 = $nodes | Where-Object { $_.type -in @('DATASET_INPUT','DATASET_OUTPUT','DATASET') }
foreach ($d in $dsNodes2) {
    $dsn = $d.id -replace '"',''
    $dsnSafe = $dsn -replace '[^A-Z0-9]','_'
    $readSteps    = if ($dsReadByMap.ContainsKey($dsn))    { $dsReadByMap[$dsn] }    else { @() }
    $writtenSteps = if ($dsWrittenByMap.ContainsKey($dsn)) { $dsWrittenByMap[$dsn] } else { @() }
    $usedByJobs   = if ($dsUsedByJobsMap.ContainsKey($dsn)) { @($dsUsedByJobsMap[$dsn]) | Sort-Object } else { @() }

    $dtLabel = switch ($d.type) {
        'DATASET_INPUT'  { 'entrada (solo lectura)' }
        'DATASET_OUTPUT' { 'salida (escritura)' }
        default          { 'uso mixto/desconocido' }
    }
    $lreclText = if ($d.lrecl) { " Longitud de registro (LRECL): $($d.lrecl)." } else { "" }
    $jobsText  = if ($usedByJobs.Count -gt 0) { " Utilizado por los JOBs: $($usedByJobs -join ', ')." } else { "" }

    $readText    = if ($readSteps.Count    -gt 0) { " Leido por $($readSteps.Count) steps: $($readSteps -join ', ')." }    else { "" }
    $writtenText = if ($writtenSteps.Count -gt 0) { " Escrito por $($writtenSteps.Count) steps: $($writtenSteps -join ', ')." } else { "" }
    if ($readSteps.Count -eq 0 -and $writtenSteps.Count -eq 0) { $readText = " No tiene steps asociados registrados." }

    $text = "Dataset $dsn de tipo $dtLabel.$lreclText$jobsText$readText$writtenText"
    $textJson = Json-Str $text

    $rArr      = '[' + (($readSteps    | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $wArr      = '[' + (($writtenSteps | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'
    $jobsArr   = '[' + (($usedByJobs   | ForEach-Object { '"'+(Json-Str $_)+'"' }) -join ',') + ']'

    $ragLines.Add('{"id":"JCL_DS_' + $dsnSafe + '","text":"' + $textJson + '","metadata":{"type":"jcl_dataset","dataset_id":"' + (Json-Str $dsn) + '","dsn_name":"' + (Json-Str $dsn) + '","dataset_type":"' + $d.type + '","lrecl":"' + $d.lrecl + '","read_by_steps":' + $rArr + ',"written_by_steps":' + $wArr + ',"used_by_jobs":' + $jobsArr + '}}')
}

[System.IO.File]::WriteAllLines("$outputDir\rag_jcl.jsonl", $ragLines)
Write-Host "=== rag_jcl.jsonl: $($ragLines.Count) documentos RAG ==="
Write-Host "  Jobs=$(@($ragLines | Where-Object {$_ -like '*"type":"jcl_job"*'}).Count) | Steps=$(@($ragLines | Where-Object {$_ -like '*"type":"jcl_step"*'}).Count) | Datasets=$(@($ragLines | Where-Object {$_ -like '*"type":"jcl_dataset"*'}).Count)"

# ── Resumen ────────────────────────────────────────────────────────────────
Write-Host "`n Archivos generados en: $outputDir"
Get-ChildItem $outputDir -Filter "*.csv" |
    Select-Object Name, @{n='Filas';e={([System.IO.File]::ReadAllLines($_.FullName)).Length - 1}} |
    Format-Table -AutoSize

# Estadisticas
$totalJobs  = @($nodes | Where-Object {$_.type -eq 'JOB'}).Count
$totalSteps = @($nodes | Where-Object {$_.type -eq 'STEP'}).Count
$totalProgs = $usedPgms.Count
$dsIn   = @($nodes | Where-Object {$_.type -eq 'DATASET_INPUT'}).Count
$dsOut  = @($nodes | Where-Object {$_.type -eq 'DATASET_OUTPUT'}).Count
$dsGen  = @($nodes | Where-Object {$_.type -eq 'DATASET'}).Count

Write-Host "ESTADISTICAS:"
Write-Host "  JOBs procesados : $totalJobs"
Write-Host "  STEPs totales   : $totalSteps"
Write-Host "  Programas custom: $totalProgs"
Write-Host "  Datasets entrada: $dsIn"
Write-Host "  Datasets salida : $dsOut"
Write-Host "  Datasets generic: $dsGen"
Write-Host ""
Write-Host "Utilitarios por familia:"
$utilFamCount = @{}
foreach ($sid in $stepExecMap.Keys) {
    $info = $stepExecMap[$sid]
    if ($info.exec_type -eq 'UTILITY') {
        $fam = if($utilDef.ContainsKey($info.target)){$utilDef[$info.target].family}else{'MISC'}
        if (!$utilFamCount.ContainsKey($fam)) { $utilFamCount[$fam]=0 }
        $utilFamCount[$fam]++
    }
}
$utilFamCount.GetEnumerator() | Sort-Object Name | ForEach-Object { Write-Host "  $($_.Key): $($_.Value)" }
