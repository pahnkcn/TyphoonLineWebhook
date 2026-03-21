"""
Database optimization module for TyphoonLineWebhook chatbot
Implements performance improvements based on codebase analysis
"""
import logging
import time
from mysql.connector import Error as MySQLError
from typing import Dict, Any, List, Tuple
from .database_manager import DatabaseManager
from .utils import safe_db_operation

class DatabaseOptimizer:
    """
    Database optimization class for implementing performance improvements
    """
    
    def __init__(self, db_manager: DatabaseManager):
        """
        Initialize the database optimizer
        
        Args:
            db_manager: DatabaseManager instance
        """
        self.db = db_manager
        
    @safe_db_operation
    def add_missing_indexes(self) -> bool:
        """
        Add missing database indexes for frequently queried columns
        Based on performance analysis recommendations
        
        Returns:
            bool: True if successful
        """
        indexes_to_add = [
            # Conversations table indexes
            {
                'table': 'conversations',
                'index_name': 'idx_conversations_user_timestamp',
                'columns': '(user_id, timestamp)',
                'query': '''
                    CREATE INDEX idx_conversations_user_timestamp 
                    ON conversations(user_id, timestamp)
                '''
            },
            {
                'table': 'conversations',
                'index_name': 'idx_conversations_risk_level',
                'columns': '(important_flag, timestamp)',
                'query': '''
                    CREATE INDEX idx_conversations_risk_level 
                    ON conversations(important_flag, timestamp)
                '''
            },
            {
                'table': 'conversations',
                'index_name': 'idx_conversations_token_count',
                'columns': '(token_count)',
                'query': '''
                    CREATE INDEX idx_conversations_token_count 
                    ON conversations(token_count)
                '''
            },
            # Registration codes table indexes
            {
                'table': 'registration_codes',
                'index_name': 'idx_registration_created_at',
                'columns': '(created_at)',
                'query': '''
                    CREATE INDEX idx_registration_created_at 
                    ON registration_codes(created_at)
                '''
            },
            {
                'table': 'registration_codes',
                'index_name': 'idx_registration_status_created',
                'columns': '(status, created_at)',
                'query': '''
                    CREATE INDEX idx_registration_status_created 
                    ON registration_codes(status, created_at)
                '''
            },
            # Follow ups table additional indexes
            {
                'table': 'follow_ups',
                'index_name': 'idx_follow_ups_created_status',
                'columns': '(created_at, status)',
                'query': '''
                    CREATE INDEX idx_follow_ups_created_status 
                    ON follow_ups(created_at, status)
                '''
            },
            # User metrics table indexes
            {
                'table': 'user_metrics',
                'index_name': 'idx_user_metrics_timestamp',
                'columns': '(timestamp)',
                'query': '''
                    CREATE INDEX idx_user_metrics_timestamp 
                    ON user_metrics(timestamp)
                '''
            },
        ]
        
        success_count = 0
        for index_info in indexes_to_add:
            try:
                # Check if table exists
                if not self.db.table_exists(index_info['table']):
                    logging.warning(f"Table {index_info['table']} does not exist, skipping index creation")
                    continue
                
                # Check if index already exists
                if self._index_exists(index_info['table'], index_info['index_name']):
                    logging.info(f"Index {index_info['index_name']} already exists on {index_info['table']}")
                    success_count += 1
                    continue
                
                # Create the index
                logging.info(f"Creating index {index_info['index_name']} on {index_info['table']}{index_info['columns']}")
                start_time = time.time()
                
                self.db.execute_and_commit(index_info['query'])
                
                execution_time = time.time() - start_time
                logging.info(f"Index {index_info['index_name']} created successfully in {execution_time:.2f} seconds")
                success_count += 1
                
            except MySQLError as e:
                if self._is_duplicate_index_error(e):
                    logging.info(f"Index {index_info['index_name']} already exists on {index_info['table']}")
                    success_count += 1
                    continue

                logging.error(f"Failed to create index {index_info['index_name']}: {str(e)}")
                continue

            except Exception as e:
                logging.error(f"Failed to create index {index_info['index_name']}: {str(e)}")
                continue
        
        logging.info(f"Database index optimization completed: {success_count}/{len(indexes_to_add)} indexes processed")
        return success_count == len(indexes_to_add)
    
    def _index_exists(self, table_name: str, index_name: str) -> bool:
        """
        Check if an index exists on a table
        
        Args:
            table_name: Name of the table
            index_name: Name of the index
            
        Returns:
            bool: True if index exists
        """
        try:
            query = """
                SELECT COUNT(*) as count
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = %s 
                AND INDEX_NAME = %s
            """
            result = self.db.execute_query(query, (table_name, index_name))
            return result[0][0] > 0 if result else False
        except Exception as e:
            logging.warning(f"Could not check if index {index_name} exists: {str(e)}")
            return False

    def _is_duplicate_index_error(self, error: Exception) -> bool:
        """Determine if the given error indicates an index already exists"""
        if isinstance(error, MySQLError) and getattr(error, 'errno', None) == 1831:
            return True
        return 'duplicate index' in str(error).lower()

    
    @safe_db_operation
    def analyze_table_performance(self) -> Dict[str, Any]:
        """
        Analyze table performance and provide optimization recommendations
        
        Returns:
            Dict: Performance analysis results
        """
        analysis_results = {}
        
        tables_to_analyze = ['conversations', 'follow_ups', 'user_metrics', 'registration_codes']
        
        for table in tables_to_analyze:
            try:
                if not self.db.table_exists(table):
                    continue
                    
                # Get table statistics
                table_stats = self._get_table_statistics(table)
                
                # Get index usage statistics
                index_stats = self._get_index_statistics(table)
                
                # Analyze query performance
                slow_queries = self._identify_slow_queries(table)
                
                analysis_results[table] = {
                    'table_stats': table_stats,
                    'index_stats': index_stats,
                    'slow_queries': slow_queries,
                    'recommendations': self._generate_recommendations(table, table_stats, index_stats)
                }
                
            except Exception as e:
                logging.error(f"Error analyzing table {table}: {str(e)}")
                analysis_results[table] = {'error': str(e)}
        
        return analysis_results
    
    def _get_table_statistics(self, table_name: str) -> Dict[str, Any]:
        """Get basic table statistics"""
        try:
            # Get row count and size information
            query = """
                SELECT 
                    table_rows,
                    ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb,
                    ROUND((data_length / 1024 / 1024), 2) AS data_mb,
                    ROUND((index_length / 1024 / 1024), 2) AS index_mb
                FROM information_schema.TABLES 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """
            result = self.db.execute_query(query, (table_name,))
            
            if result:
                return {
                    'row_count': result[0][0] or 0,
                    'total_size_mb': result[0][1] or 0,
                    'data_size_mb': result[0][2] or 0,
                    'index_size_mb': result[0][3] or 0
                }
        except Exception as e:
            logging.warning(f"Could not get statistics for table {table_name}: {str(e)}")
        
        return {}
    
    def _get_index_statistics(self, table_name: str) -> List[Dict[str, Any]]:
        """Get index statistics for a table"""
        try:
            query = """
                SELECT 
                    INDEX_NAME,
                    COLUMN_NAME,
                    CARDINALITY,
                    SUB_PART,
                    NULLABLE
                FROM INFORMATION_SCHEMA.STATISTICS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = %s
                ORDER BY INDEX_NAME, SEQ_IN_INDEX
            """
            result = self.db.execute_query(query, (table_name,))
            
            indexes = []
            current_index = None
            
            for row in result:
                index_name, column_name, cardinality, sub_part, nullable = row
                
                if current_index is None or current_index['name'] != index_name:
                    if current_index:
                        indexes.append(current_index)
                    current_index = {
                        'name': index_name,
                        'columns': [column_name],
                        'cardinality': cardinality,
                        'nullable': nullable == 'YES'
                    }
                else:
                    current_index['columns'].append(column_name)
            
            if current_index:
                indexes.append(current_index)
            
            return indexes
            
        except Exception as e:
            logging.warning(f"Could not get index statistics for table {table_name}: {str(e)}")
            return []
    
    def _identify_slow_queries(self, table_name: str) -> List[str]:
        """Identify potentially slow queries for a table"""
        # This would typically require the slow query log or Performance Schema
        # For now, return common slow query patterns
        slow_patterns = []
        
        if table_name == 'conversations':
            slow_patterns = [
                "SELECT without user_id filter",
                "ORDER BY timestamp without index",
                "Full text search without index"
            ]
        elif table_name == 'follow_ups':
            slow_patterns = [
                "SELECT without status filter",
                "Date range queries without index"
            ]
        
        return slow_patterns
    
    def _generate_recommendations(self, table_name: str, table_stats: Dict, index_stats: List) -> List[str]:
        """Generate optimization recommendations for a table"""
        recommendations = []
        
        # Check table size
        if table_stats.get('total_size_mb', 0) > 100:
            recommendations.append("Consider table partitioning for large table")
        
        # Check index to data ratio
        data_size = table_stats.get('data_size_mb', 0)
        index_size = table_stats.get('index_size_mb', 0)
        
        if data_size > 0 and (index_size / data_size) > 0.5:
            recommendations.append("High index-to-data ratio - review unused indexes")
        
        # Check for missing indexes on large tables
        if table_stats.get('row_count', 0) > 10000:
            if table_name == 'conversations':
                has_user_timestamp_idx = any(
                    'user_id' in idx['columns'] and 'timestamp' in idx['columns'] 
                    for idx in index_stats
                )
                if not has_user_timestamp_idx:
                    recommendations.append("Add composite index on (user_id, timestamp)")
        
        return recommendations
    
    @safe_db_operation
    def optimize_table_maintenance(self) -> bool:
        """
        Perform table maintenance operations
        
        Returns:
            bool: True if successful
        """
        tables = ['conversations', 'follow_ups', 'user_metrics', 'registration_codes']
        success_count = 0
        
        for table in tables:
            try:
                if not self.db.table_exists(table):
                    continue
                
                # Analyze table with proper result handling
                logging.info(f"Analyzing table {table}...")
                with self.db.get_cursor() as cursor:
                    cursor.execute(f"ANALYZE TABLE {table}")
                    # Consume all results to avoid "Unread result found" error
                    results = cursor.fetchall()
                    logging.debug(f"ANALYZE TABLE {table} results: {results}")
                
                # Optimize table with proper result handling
                logging.info(f"Optimizing table {table}...")
                with self.db.get_cursor() as cursor:
                    cursor.execute(f"OPTIMIZE TABLE {table}")
                    # Consume all results to avoid "Unread result found" error
                    results = cursor.fetchall()
                    logging.debug(f"OPTIMIZE TABLE {table} results: {results}")
                
                success_count += 1
                logging.info(f"Table {table} maintenance completed")
                
            except Exception as e:
                logging.error(f"Table maintenance failed for {table}: {str(e)}")
                continue
        
        logging.info(f"Table maintenance completed: {success_count}/{len(tables)} tables processed")
        return success_count == len(tables)

def optimize_database(config: Dict[str, Any]) -> bool:
    """
    Main function to optimize database performance
    
    Args:
        config: Database configuration
        
    Returns:
        bool: True if optimization successful
    """
    try:
        # Create database manager with optimized settings
        db_manager = DatabaseManager(config, pool_size=32)  # Maximum allowed pool size
        optimizer = DatabaseOptimizer(db_manager)
        
        logging.info("Starting database optimization process...")
        
        # Step 1: Add missing indexes
        logging.info("Phase 1: Adding missing database indexes...")
        indexes_success = optimizer.add_missing_indexes()
        
        # Step 2: Analyze table performance
        logging.info("Phase 2: Analyzing table performance...")
        performance_analysis = optimizer.analyze_table_performance()
        
        # Log performance analysis results
        for table, analysis in performance_analysis.items():
            if 'error' in analysis:
                logging.error(f"Analysis failed for {table}: {analysis['error']}")
                continue
                
            stats = analysis.get('table_stats', {})
            recommendations = analysis.get('recommendations', [])
            
            logging.info(f"Table {table}: {stats.get('row_count', 0)} rows, "
                        f"{stats.get('total_size_mb', 0)} MB total size")
            
            if recommendations:
                logging.info(f"Recommendations for {table}: {', '.join(recommendations)}")
        
        # Step 3: Perform table maintenance
        logging.info("Phase 3: Performing table maintenance...")
        maintenance_success = optimizer.optimize_table_maintenance()
        
        overall_success = indexes_success and maintenance_success
        
        if overall_success:
            logging.info("Database optimization completed successfully")
        else:
            logging.warning("Database optimization completed with some issues")
        
        return overall_success
        
    except Exception as e:
        logging.error(f"Database optimization failed: {str(e)}")
        return False