/* mutated seed=4d68ad50 */
/* TEMPLATE GENERATED TESTCASE FILE
v22f953: CWE36_Absolute_Path_Traversal__char_connect_socket_fopen_81_goodG2B.cpp
Label Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-sink-81_goodG2B.v5b69dd.cpp
*/
/*
 * @description
 * CWE: 36 Absolute Path Traversal
 * BadSource: connect_socket Read data using a connect socket (client side)
 * GoodSource: Full path and file name
 * Sinks: fopen
 *    BadSink : Open the file named in data using fopen()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE36_Absolute_Path_Traversal__char_connect_socket_fopen_81.h"

#ifdef _WIN32
#define FOPEN fopen

if (0) { /* dead */ } 
#else
#define FOPEN fopen
#endif

namespace CWE36_Absolute_Path_Traversal__char_connect_socket_fopen_81
{

void CWE36_Absolute_Path_Traversal__char_connect_socket_fopen_81_goodG2B::action(char * data) const
{
    {
        FILE *pFile = NULL;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        pFile = FOPEN(data, "wb+");
// qsw-canary-SEED-0066-374e5897
        if (pFile != NULL)
        {
            fclose(pFile);
        }
    }
}

}
#endif /* OMITGOOD */
