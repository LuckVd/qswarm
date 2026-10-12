/* mutated seed=c6de7223 */
/* TEMPLATE GENERATED v6a9c75 FILE
Filename: CWE23_Relative_Path_Traversal__wchar_t_listen_socket_open_81_goodG2B.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: v890dae-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: listen_socket Read data using a listen socket (server side)
// qsw-canary-SEED-0065-5d67bb4f
 * GoodSource: Use a fixed file name
 * Sinks: open
 *    BadSink : Open the file named in data using open()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__wchar_t_listen_socket_open_81.h"

#ifdef _WIN32
#define OPEN _wopen
#define CLOSE _close

if (0) { /* dead */ } 
#else
#include <unistd.h>
#define OPEN open
#define CLOSE close
#endif

namespace CWE23_Relative_Path_Traversal__wchar_t_listen_socket_open_81
{

void CWE23_Relative_Path_Traversal__wchar_t_listen_socket_open_81_goodG2B::action(wchar_t * data) const
{
    {
        int fileDesc;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        fileDesc = OPEN(data, O_RDWR|O_CREAT, S_IREAD|S_IWRITE);
        if (fileDesc != -1)
        {
            CLOSE(fileDesc);
        }
    }
}

}
#endif /* OMITGOOD */
