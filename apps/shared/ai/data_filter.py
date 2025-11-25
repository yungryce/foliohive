# Expand the TECHNICAL_TERMS dictionary for more comprehensive matches
# including: common file extensions, version patterns, and technical terms.
import re
from typing import List


# Check for advanced skills keywords
advanced_skills = {
    # Cloud & DevOps
    'devops', 'cloud native', 'aws', 'azure', 'gcp', 'terraform', 
    'kubernetes', 'docker', 'helm', 'gitops', 'ci/cd', 'iac',
    'microservices', 'serverless', 'event-driven', 'distributed systems',
    'domain-driven design', 'hexagonal architecture', 'cqrs', 'event sourcing',
    'graphql', 'grpc', 'websockets', 'service mesh', 'api gateway',
    'oauth', 'jwt', 'openid', 'message broker', 'kafka', 'rabbitmq',
    'data pipeline', 'etl', 'spark', 'hadoop', 'airflow', 'machine learning',
    'neural networks', 'nlp', 'computer vision', 'data warehouse',
    'nosql', 'mongodb', 'cassandra', 'redis', 'elasticsearch', 'neo4j',
    'database sharding', 'replication', 'data modeling',
    'security', 'penetration testing', 'oauth2', 'encryption', 'zero trust',
    'container security', 'threat modeling', 'devsecops',
    'webassembly', 'pwa', 'microfrontends', 'state management',
    'ssr', 'graphql client', 'web workers',
    'high availability', 'fault tolerance', 'scalability', 'resilience',
    'chaos engineering', 'load balancing', 'cdn', 'edge computing'
}

# Common technical terms (broad and useful in tech contexts)
technical_keywords = {
    'api', 'rest', 'restful', 'graphql', 'grpc', 'rpc', 'sdk', 'cli', 'ui', 'ux',
    'gui', 'ide', 'repl', 'shell', 'terminal', 'console', 'script', 'code', 'repo',
    'git', 'github', 'gitlab', 'bitbucket', 'commit', 'push', 'pull', 'merge',
    'branch', 'checkout', 'ci', 'cd', 'cicd', 'devops', 'sre', 'infra', 'ops',
    'cloud', 'aws', 'azure', 'gcp', 'vm', 'vmss', 'vnet', 'subnet', 'dns', 'ip',
    'serverless', 'microservices', 'container', "containerization", 'orchestration',
    'image', 'containerd',
    'cosmosdb', 'sqlalchemy', 'blob', 'cache', 'queue', 'streaming',
    'kafka', 'memcached', 'nosql', 
    'http', 'https', 'tcp', 'udp', 'tls', 'ssl', 'ssh', 'vpn', 'nginx', 'apache',
    'loadbalancer', 'proxy', 'reverseproxy', 'firewall', 'routing', 'gateway',
    'docker', 'kubernetes', 'k8s', 'pod', 'cluster', 'aks', 'helm',
    'terraform', 'ansible', 'chef', 'puppet', 'vault', 'consul', 'nomad',
    'jenkins', 'githubactions', 'gitlabci', 'travisci', 'circleci',
    'mysql', 'mariadb', 'postgres', 'postgresql', 'sqlite', 'mssql', 'oracle',
    'mongodb', 'redis', 'cassandra', 'dynamodb', 'influxdb', 'elasticsearch',
    'kafka', 'rabbitmq', 'sqs', 'pubsub', 'eventhub', 'eventgrid',
    'log', 'logging', 'monitoring', 'observability', 'prometheus', 'grafana',
    'datadog', 'newrelic', 'sentry', 'jaeger', 'zipkin',
    'flask', 'django', 'fastapi', 'express', 'koa', 'hapi',
    'node', 'nodejs', 'npm', 'yarn', 'pnpm',
    'react', 'angular', 'vue', 'svelte', 'nextjs', 'nuxt', 'remix', 'astro',
    'webpack', 'vite', 'babel', 'eslint', 'prettier',
    'typescript', 'javascript', 'python', 'perl', 'java', 'csharp', 'dotnet',
    'ruby', 'rails', 'php', 'laravel', 'go', 'rust', 'c', 'cpp', 'haskell',
    'ml', 'ai', 'machinelearning', 'deeplearning', 'neuralnetwork',
    'pytorch', 'tensorflow', 'sklearn', 'cv', 'nlp',
    'blockchain', 'cryptocurrency', 'bitcoin', 'ethereum', 'solidity',
    'json', 'yaml', 'toml', 'env', 'config',
    'test', 'unittest', 'integration', 'e2e', 'coverage', 'tdd', 'bdd',
    'automation', 'scripting', 'debug', 'profile', 'trace', 'benchmark',
    'build', 'compile', 'deploy', 'release', 'artifact',
    'linux', 'windows', 'macos', 'ubuntu', 'debian', 'arch', 'fedora', 'centos',
}

complexity_indicators = [
    'authentication', 'authorization', 'caching', 'concurrency',
    'database', 'error handling', 'logging', 'messaging', 'security',
    'transactions', 'validation',
    'multithreading', 'thread safety', 'mutex', 'semaphore', 'lock',
    'deadlock prevention', 'race condition', 'atomic operations', 'synchronization',
    'async/await', 'coroutines', 'futures', 'promises', 'reactive programming',
    'circuit breaker', 'retry mechanism', 'backoff strategy', 'fallback',
    'graceful degradation', 'exception handling', 'defensive programming',
    'fault isolation', 'error boundary', 'recovery mechanism',
    'connection pooling', 'object pooling', 'memory pooling', 'resource pooling',
    'lazy loading', 'eager loading', 'memoization', 'code optimization',
    'query optimization', 'indexing strategy', 'query plan', 'execution plan',
    'jit compilation', 'hot path optimization',
    'state machine', 'state transition', 'immutable state', 'state persistence', 
    'global state', 'context management', 'session management', 'lifecycle hooks',
    'hydration', 'dehydration',
    'stream processing', 'batch processing', 'binary data', 'blob handling',
    'serialization', 'deserialization', 'compression', 'encryption',
    'data transformation', 'data validation', 'schema validation',
    'webhook handling', 'callback processing', 'polling mechanism', 'long polling',
    'push notification', 'ipc', 'inter-process communication', 'cross-origin',
    'idempotency', 'consistency guarantee',
    'dependency injection', 'aop', 'aspect-oriented', 'reflection', 'introspection',
    'metaprogramming', 'code generation', 'plugin system', 'extension mechanism',
    'hot reload', 'dynamic loading', 'monkey patching',
    'input sanitization', 'output encoding', 'csrf protection', 'xss prevention',
    'sql injection', 'rate limiting', 'throttling', 'acl', 'rbac', 'abac',
    'permission system',
    'mock object', 'test double', 'stub', 'spy', 'test fixture', 'test harness',
    'property-based testing', 'fuzz testing', 'mutation testing', 'bdd', 'tdd',
    'connection management', 'keepalive', 'multiplexing', 'protocol negotiation',
    'handshaking', 'header compression', 'binary protocol', 'custom protocol',
    'socket programming', 'network buffer'
]

# Common file extensions
file_extensions = {
    # Programming languages
    'c', 'cpp', 'h', 'hpp', 'cs', 'java', 'py', 'rb', 'js', 'ts', 'rs', 'go', 'php',
    'swift', 'kt', 'kts', 'm', 'scala', 'lua', 'sh', 'bat', 'pl',
    'html', 'htm', 'xhtml', 'xml', 'css', 'scss', 'sass', 'less',
    'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'csv', 'tsv', 'sql',
    'md', 'txt', 'rst', 'pdf', 'doc', 'docx', 'odt', 'rtf', 'tex',
    'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'ico', 'tiff', 'tif',
    'mp3', 'wav', 'ogg', 'flac', 'aac', 'mp4', 'mov', 'avi', 'mkv', 'webm',
    'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'zst',
    'dockerfile', 'makefile', 'gitignore', 'editorconfig', 'env', 'npmrc',
}

# Version regex patterns (used programmatically)
version_patterns = [
    r'^v?\d+(\.\d+)*$',  # v1, 1.0.0, 2.0, etc.
    r'^\d{4}$',          # Year-style versions: 2022
    r'^\d{4}\.\d{2}$',   # Ubuntu-style: 20.04
    r'^\d+\.\d+\.\d+$',  # SemVer strict
    r'^\d+\.\d+[a-z]$',  # 3.2b, 1.0a
]

# Common stop words to filter out
stop_words = {
    'the', 'and', 'for', 'are', 'can', 'you', 'get', 'use', 'show', 'find',
    'list', 'give', 'tell', 'make', 'have', 'need', 'what', 'how', 'when',
    'where', 'why', 'which', 'who', 'with', 'from', 'this', 'that', 'they',
    'them', 'will', 'would', 'could', 'should', 'may', 'might', 'must',
    'been', 'being', 'done', 'does', 'did', 'has', 'had', 'was', 'were',
    'all', 'any', 'some', 'more', 'most', 'many', 'much', 'new', 'old',
    'good', 'bad', 'big', 'small', 'high', 'low', 'long', 'short', 'way',
    'time', 'work', 'help', 'about', 'over', 'under', 'between', 'through',
    'into', 'onto', 'upon', 'during', 'before', 'after', 'above', 'below',
    'then', 'than', 'else', 'only', 'also', 'even', 'still', 'yet', 'just',
    'now', 'here', 'there', 'home', 'back', 'out', 'off', 'down', 'away'
}

# Optimized structured result with precompiled regex patterns
technical_terms_structured = {
    "advanced_skills": frozenset(advanced_skills),  # Use frozenset for O(1) lookups
    "complexity_indicators": frozenset(complexity_indicators),  # Use
    "file_extensions": frozenset(file_extensions),  # Use frozenset for O(1) lookups
    "version_patterns": [re.compile(pattern) for pattern in version_patterns],  # Precompile regex
    "domain": frozenset(technical_keywords),  # Use frozenset for O(1) lookups
    "stop_words": frozenset(stop_words),  # Use frozenset for O(1) lookups
}

def extract_language_terms(query: str) -> List[str]:
    """Extract programming language terms from a query with improved detection using regex."""
    # Enhanced programming languages and their variations
    language_patterns = {
        'python': [r'\bpython\b', r'\bpy\b', r'\bdjango\b', r'\bflask\b', r'\bfastapi\b', r'\bpandas\b', r'\bnumpy\b', r'\bpytorch\b', r'\btensorflow\b'],
        'javascript': [r'\bjavascript\b', r'\bjs\b', r'\bnode\b', r'\bnodejs\b', r'\breact\b', r'\bvue\b', r'\bangular\b', r'\bexpress\b', r'\bnext\b'],
        'typescript': [r'\btypescript\b', r'\bts\b'],
        'java': [r'\bjava\b', r'\bspring\b', r'\bspringboot\b', r'\bmaven\b', r'\bgradle\b'],
        'c++': [r'\bc\+\+\b', r'\bcpp\b', r'\bcplus\b'],
        'c#': [r'\bc#\b', r'\bcsharp\b', r'\bdotnet\b', r'\b\.net\b'],
        'c': [r'\bc\b', r'\bc language\b', r'\bbrainfuck\b', r'\blimbo\b', r'\bm\b'],  # Added regex for 'c'
        'php': [r'\bphp\b', r'\blaravel\b', r'\bsymfony\b', r'\bcomposer\b', r'\bblade\b'],
        'go': [r'\bgolang\b', r'\bgo\b'],
        'rust': [r'\brust\b'],
        'kotlin': [r'\bkotlin\b'],
        'swift': [r'\bswift\b'],
        'ruby': [r'\bruby\b', r'\brails\b'],
        'scala': [r'\bscala\b'],
        'r': [r'\br\b', r'\br language\b', r'\brstudio\b'],  # Added regex for 'r'
        'matlab': [r'\bmatlab\b'],
        'sql': [r'\bsql\b', r'\bmysql\b', r'\bpostgresql\b', r'\bsqlite\b', r'\bmongodb\b'],
        'html': [r'\bhtml\b', r'\bhtml5\b'],
        'css': [r'\bcss\b', r'\bcss3\b', r'\bscss\b', r'\bsass\b'],
        'shell': [r'\bshell\b', r'\bbash\b', r'\bsh\b'],
        'powershell': [r'\bpowershell\b', r'\bps1\b'],
        'dockerfile': [r'\bdocker\b', r'\bdockerfile\b', r'\bdocker-compose\b'],
        'yaml': [r'\byaml\b', r'\byml\b'],
        'json': [r'\bjson\b'],
        'xml': [r'\bxml\b'],
        'hcl': [r'\bhcl\b'],
        'jinja': [r'\bjinja\b'],
        'mako': [r'\bmako\b'],
        'procfile': [r'\bprocfile\b'],
        'assembly': [r'\bassembly\b'],
        'puppet': [r'\bpuppet\b']
    }
    
    query_lower = query.lower()
    found_languages = []
    
    for language, patterns in language_patterns.items():
        for pattern in patterns:
            if re.search(pattern, query_lower):
                found_languages.append(language)
                break  # Only add each language once
    
    return found_languages


